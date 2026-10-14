# Databricks SQL (DBSQL)

> Includes SQL Warehouses. Databricks SQL is the umbrella product; SQL Warehouses are the compute under it.

## Level 100 — What it is

DBSQL is the **lakehouse data warehouse** surface inside Databricks: serverless SQL warehouses, a SQL editor, dashboards, alerts, and a query history. You query Unity Catalog tables with ANSI SQL; the engine underneath is Photon-accelerated Spark SQL on Delta.

- **Warehouses are the compute**: serverless, pro, classic — all SQL-optimised.
- **ANSI SQL**: standard SQL with Databricks extensions (`ai_query`, pipe syntax, geo, AI functions).
- **Lakehouse-native BI**: query, share, schedule, alert, dashboard — all in UC.

## Level 200 — The product surface

- **SQL Warehouses** (compute):
  - **Serverless** — Databricks-managed; fastest startup; recommended.
  - **Pro** — in your VPC/VNet; supports advanced governance, Private Link, customer-managed VPCs.
  - **Classic** — legacy in-your-VPC; avoid for new work.
  - Size from 2X-Small to 4X-Large; auto-scale clusters (1-N); auto-stop after idle.
- **SQL editor** with multi-query tabs, parameter widgets, query history, version snippets.
- **Queries** — saved, parameterised, can be referenced by dashboards and alerts.
- **Dashboards** (AI/BI Dashboards) — but they have their own product page.
- **Alerts** — schedule a query, evaluate a condition (`> X`, `IS NULL`), notify via email/Slack/webhook.
- **Statement Execution API** — REST API to submit SQL without JDBC/ODBC; results returned inline or via cloud-fetch URL.
- **JDBC/ODBC drivers** for BI tools (Tableau, Power BI, Looker, etc.).
- **AI Functions**: `ai_query`, `ai_classify`, `ai_extract`, `ai_gen` — call foundation models from SQL.
- **Advanced SQL**: pipe syntax (`|>`), recursive CTEs, multi-statement transactions, stored procedures (GA, Runtime 17.0+ / UC only), materialized views, streaming tables, `read_files()`, `http_request()`, Lakehouse Federation.
- **Metric Views**: semantic-layer business metrics defined once in UC, queryable from DBSQL with the `MEASURE` aggregate function; first-class dataset source in AI/BI Dashboards and Genie Agents.

## Level 300 — Architecture

```
[BI tool / SQL editor / API] ─► [SQL Warehouse cluster] ─► [Photon SQL engine] ─► UC tables (Delta + Federation)
        (JDBC/ODBC/REST)              (driver + N workers,            (vectorized,
                                       auto-scaling)                   query opt, caching)
```

**Warehouse internals:** a SQL Warehouse is a long-lived autoscaling group of Spark workers tuned for low-latency SQL. Serverless runs in Databricks' account; Pro/Classic in yours. **Photon is on by default**.

**Caching**:
- **Result cache** — repeat exact-query results in TTL window are returned instantly.
- **Disk cache** (formerly Delta cache) — Parquet/Delta files cached on local SSD; warm warehouses serve hot data from disk cache.
- **Query plan cache** — for parameterised queries.

**Concurrency**: each warehouse handles multiple concurrent queries; autoscaling adds clusters when concurrency exceeds a threshold.

**Auth + UC**: per-query credentials resolve against UC; row filters / column masks apply; lineage logged.

## Level 400 — Deep internals

### Warehouse lifecycle

- A warehouse is a managed cluster pool with a stable endpoint URL.
- States: stopped → starting → running → stopping. Serverless: ~5–15 s start. Pro: 60–180 s start.
- Idle auto-stop after configured minutes (default 10–30, tunable down to 1 on serverless).
- Multi-cluster mode: warehouse scales from `min_num_clusters` to `max_num_clusters` based on query queue depth + age. Each cluster handles a subset of concurrent queries.
- **Sizes** map to fixed worker counts and memory. Bigger size ≠ more concurrency; bigger size = faster per-query.

### Result and disk cache

- **Result cache** is keyed on the (query text + UC table version + parameters). Hits return ~ms latency. Invalidated by any underlying table change.
- **Disk cache** is local NVMe on workers; Parquet decoded into a fast format. Survives across queries on the same warehouse worker as long as the worker lives.
- **Photon stats + min/max pruning** is the third layer — even on a cold cache, Photon skips files that can't match.

### Query lifecycle

- Parse (Spark Catalyst grammar with Databricks extensions).
- Resolve (UC catalog lookup, row/column policy).
- Optimise (cost-based + heuristic rules).
- Execute (Photon physical plan).
- Stream results to client; large results spill to "cloud fetch" — staged in cloud storage with a pre-signed URL that the client downloads.
- Logged in `system.query.history`.

### Statement Execution API

- `POST /api/2.0/sql/statements` — submit SQL.
- Three result formats: inline (small), arrow stream, external links (cloud-fetch).
- For large results, **external links** is the right mode: results land in cloud storage; client fetches in parallel.
- Async-friendly: `disposition=EXTERNAL_LINKS, wait_timeout=10s`, poll if needed.

### Lakehouse Federation through DBSQL

- Foreign catalogs (Snowflake, Postgres, etc.) appear in UC; queries with foreign tables get pushdown where possible.
- Pushdown failures fall back to data movement; large federated joins can pull lots of bytes.

### AI Functions

- `ai_query(endpoint_name, prompt_or_table_col)` calls a Model Serving endpoint from SQL.
- `ai_classify` / `ai_extract` / `ai_gen` wrap common patterns.
- Token usage flows through Unity Gateway (formerly Mosaic AI Gateway) when configured.
- Latency is dominated by the model endpoint; SQL parallelises but rate limits apply.

### Streaming tables and MVs in DBSQL

- `CREATE STREAMING TABLE foo AS SELECT ... FROM read_files(...)` — creates a hidden LDP pipeline that ingests.
- `CREATE MATERIALIZED VIEW bar AS SELECT ...` — creates a hidden refreshing pipeline.
- Both managed under DBSQL UI but powered by LDP underneath.

### System tables

- `system.query.history` — every query, duration, cost, user.
- `system.compute.warehouses` — warehouse runtime state.
- `system.billing.usage` — joined with the above gives per-query DBU cost.

### Limits / realities

- Result rowset size has limits; for huge results use external-links disposition.
- Statement Execution API has rate limits per workspace.
- Serverless warehouses queue queries when over capacity; bigger size or multi-cluster mitigates.
- `ai_query` is rate-limited by the underlying endpoint; not a substitute for batch inference in big jobs.
- Stored procedures are GA as of late 2026 but require Runtime 17.0+ and Unity Catalog; language semantics differ from legacy procedural SQL.

### Mental model

DBSQL is **"a serverless data warehouse interface on top of UC + Delta + Photon"**. Warehouses are clusters with sane caching defaults. Compared to Snowflake/BigQuery, the differentiators are: cheap because compute pauses; full UC governance; SQL+Spark feature parity (UDFs, streaming, federation, AI functions); BI ecosystem via JDBC/ODBC.

---

## Anti-patterns — where you should not use DBSQL

- **Heavy ETL transforms.** Use LDP or Spark notebooks; DBSQL is for query and serve.
- **Long-running interactive Python.** SQL only here. Use notebooks.
- **Single-row OLTP lookups.** Even on serverless, latency floor is ~hundreds of ms. Use Lakebase.
- **AI batch inference via `ai_query` over millions of rows.** Use a Spark/LDP pipeline with Model Serving batch.
- **Loading from external systems you'd rather mirror.** Use Lakeflow Connect, not federated reads, for repeat workloads.
- **Pinning to a huge warehouse "just in case".** Pay for idle; small + multi-cluster is usually better.
- **Using classic warehouses for new work.** Serverless / Pro are the path forward.
- **JDBC connections kept open across long idle periods.** Connection pools should be sized; warehouses auto-stop and reconnect.
- **Result-streaming a billion rows to a client.** Use external links or persist to a UC table.
- **Treating warehouses as multi-tenant prod + dev.** Separate warehouses for predictability.

## Weaknesses — areas DBSQL does not do well

- **Cold-start on serverless still measurable.** ~5–15s. For sub-second consistent latency, keep one warehouse always-warm.
- **Multi-cluster scaling is queue-depth based.** Bursty workloads can queue before scale-up kicks in.
- **No first-class Python in queries.** Need to call a registered SQL UDF or `ai_query`, no inline Python.
- **Stored procedures require Runtime 17.0+ and UC only.** Language semantics differ from legacy procedural SQL; not all SQL dialects supported.
- **Result-cache invalidation is coarse.** Any underlying table change invalidates; can't pin to a table version.
- **Performance of federated joins is uneven.** Pushdown isn't always achieved; can pull a lot.
- **JDBC driver quirks across BI tools.** Some BI tools struggle with newer features; pin driver versions.
- **Per-query cost attribution requires system tables.** No first-class "cost of last query" widget for users.
- **`ai_query` rate limits surface as failures.** Need retry logic for large batches.
- **Cross-warehouse query handoff isn't a thing.** Each query runs on one warehouse.
- **Streaming-table refresh schedule is managed behind the scenes.** Sometimes you want manual control and don't have it.
- **Multi-statement transactions are limited.** Not full BEGIN/COMMIT semantics across all operations.

---

## Sources

- [Databricks SQL docs](https://docs.databricks.com/aws/en/sql/)
- [SQL Warehouses](https://docs.databricks.com/aws/en/compute/sql-warehouse/)
- [Statement Execution API](https://docs.databricks.com/api/workspace/statementexecution)
- [AI Functions](https://docs.databricks.com/aws/en/large-language-models/ai-functions)
- [Materialized views and streaming tables in DBSQL](https://docs.databricks.com/aws/en/views/materialized)
- [Stored procedures — CREATE PROCEDURE (GA, Runtime 17.0+)](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-create-procedure.html)
- [Metric Views](https://docs.databricks.com/en/metric-views/index.html)
- [Glossary](../Glossary/glossary.md)
