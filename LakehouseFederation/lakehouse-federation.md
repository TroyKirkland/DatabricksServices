# Lakehouse Federation

## Level 100 — What it is

Lakehouse Federation lets you query **other databases as if they were UC tables** — without copying the data. There are two distinct modes: **Query Federation** (JDBC-based pushdown to relational databases) and **Catalog Federation** (direct object-storage access via open table formats, for platforms with their own catalog services). Both surface as foreign catalogs in UC.

- **No copy**: queries hit the source live.
- **UC-governed**: foreign catalogs respect UC permissions and lineage.
- **SQL-anywhere**: write Spark SQL / DBSQL queries that span lakehouse + external sources.

## Level 200 — The product surface

- **Connection** — a UC securable storing the connection type (e.g., `SNOWFLAKE`), host, credentials. Credentials managed in UC.
- **Foreign catalog** — a UC catalog backed by a connection; mirrors the external system's schemas/tables (read-only for most sources).
- **Query Federation sources** (JDBC-based): PostgreSQL, MySQL, SQL Server, Azure Synapse, Snowflake, Amazon Redshift, Google BigQuery, Salesforce Data 360, Oracle, Teradata, Databricks (cross-workspace).
- **Catalog Federation sources** (object-storage / open table format, not JDBC): Legacy Databricks Hive metastore, External Hive metastore, AWS Glue metastore, Salesforce Data 360, Snowflake, Palantir Foundry, Workday Data Connect.
- **HTTP connection** — new connection type (Databricks Runtime 16.2+) for HTTP-based external sources.
- **Query pushdown** — Spark pushes filters, projections, aggregates to the source when possible (Query Federation mode).
- **Catalog Federation advantage** — accesses data directly in object storage via Databricks compute; more cost-effective for supported sources than pulling via JDBC.
- **Automatic schema refresh** in Catalog Federation at query time; Query Federation may require manual refresh.
- **Authoring** — Catalog Explorer UI, SQL `CREATE CONNECTION`, REST API, Terraform, or Databricks Asset Bundles.

## Level 300 — Architecture

**Query Federation (JDBC):**
```
[Spark / DBSQL query] ──► [UC Foreign Catalog resolution] ──► [JDBC connector]
                                                                       │
                                                                       ▼
                                                          [External database executes]
                                                                       │
                                                                       ▼
                                                          Result rows ──► back to Spark
```

**Catalog Federation (object storage):**
```
[Spark / DBSQL query] ──► [UC Foreign Catalog resolution] ──► [External catalog metadata]
                                                                       │
                                                                       ▼
                                                          Databricks compute reads object storage directly
                                                          (no rows cross the JDBC wire)
```

- **UC** owns the connection and credential; query authors don't see source credentials.
- **Query Federation**: Spark Catalyst negotiates pushdown (predicates, aggregates, single-source joins). What can't be pushed pulls rows into Spark.
- **Catalog Federation**: Databricks reads foreign table data from object storage using open table format metadata; no JDBC round-trip. More performant for supported sources.
- **Photon** handles post-pull / post-scan execution; large pulls still bottlenecked by network for Query Federation.
- **Permissions**: GRANT SELECT on the foreign catalog applies; UC tracks lineage and audit. Source-side permissions of the connection's identity also apply.

## Level 400 — Deep internals

### Pushdown — what works, what doesn't

- **Always push**: simple predicates (`WHERE col = ?`), projections, `LIMIT`, single-table aggregations on common functions.
- **Often push**: joins between two tables in the *same* source, common window functions.
- **Rarely push**: UDFs (Spark-only), Photon-only functions, cross-source joins (impossible — different sources, no shared engine).
- The `EXPLAIN` output shows what was pushed (e.g., `*Scan JDBCRelation` includes a pushed predicate list).
- Failure to push doesn't fail the query — it pulls rows and processes in Spark. This is the silent perf killer.

### Cross-source joins

- A query joining a foreign Snowflake table with a Delta table runs **two separate scans** (Snowflake reads + Delta reads) and joins inside Spark/Photon.
- This is the right design (Snowflake and Delta can't share a plan), but it pulls bytes over the wire. Filter aggressively before joining.

### Credential management

- The connection stores credentials in UC, encrypted with the metastore's key.
- Source-side identity is the *connection's* identity, not the query author's. This means UC enforces who can use the connection (`USE CONNECTION`), but the source-side audit shows one user.
- For per-user identity, look at workspace-specific options (e.g., Snowflake OAuth identity federation, where each user authenticates separately).

### Query lifecycle

- Parse, resolve foreign catalog references against UC's foreign-catalog metadata cache.
- Plan with pushdown rules per source type.
- Execute: source-side query runs; rows stream back; Spark continues the plan.
- Result rows accrue Spark / Photon execution cost on the warehouse side and read cost on the source side.

### Per-source quirks

- **Snowflake**: available via both Query Federation (JDBC) and Catalog Federation (object storage). JDBC path accrues Snowflake warehouse cost per query; Catalog Federation path is more cost-effective. Pushdown good but not all Snowflake-specific functions map.
- **PostgreSQL / MySQL**: solid JDBC pushdown. For high-volume / high-frequency, prefer Lakeflow Connect CDC + Delta over federation.
- **SQL Server / Azure Synapse**: similar to Postgres; double-check date/timestamp handling.
- **BigQuery**: pushdown via BQ JDBC; large pulls expensive due to BQ slot model.
- **Redshift**: data unloaded via temporary S3 area in some implementations.
- **Salesforce Data 360**: available via both Query Federation and Catalog Federation.
- **Oracle / Teradata**: supported via Query Federation (JDBC); GA as of 2026.
- **Palantir Foundry / Workday Data Connect**: Catalog Federation only (no JDBC path).
- **AWS Glue / External Hive metastores**: Catalog Federation; primary use case is incremental UC migration.

### Caching and schema refresh

- No first-class result cache for federated queries by default.
- DBSQL warehouse result cache may apply to identical federated queries.
- **Schema refresh**: Catalog Federation refreshes metadata automatically at query time (manual refresh also available). Query Federation schema changes require manual catalog refresh.
- For repeat workloads, materialise into a Delta table (via INSERT OVERWRITE / MERGE / streaming) instead of re-federating.

### Limits / realities

- Foreign catalog refresh on schema changes is not always automatic; you may need to refresh the catalog manually.
- Foreign tables are read-only for most sources; writes via federation are limited.
- Per-query cost has two components: warehouse DBU + source-side cost. Cost attribution is messy.
- Some source-specific types don't map cleanly to Spark types; expect string coercion in edge cases.
- Network egress: cross-region federation will hurt; co-locate when possible.

### Mental model

Lakehouse Federation is **two tools in one**: (1) **Query Federation** — a JDBC connection pool with UC governance and pushdown intelligence; and (2) **Catalog Federation** — a zero-copy metadata bridge for open-format catalogs. Best for: exploratory queries across heterogeneous sources, governed access to external systems, incremental UC migration, and POC without copying. Worst for: hot production paths where copy-then-query is cheaper and faster.

---

## Anti-patterns — where you should not use Lakehouse Federation

- **Production batch ETL.** Repeat federated queries cost both sides; mirror via Lakeflow Connect instead.
- **Hot dashboards.** A dashboard hitting Snowflake via federation 100×/day pays Snowflake compute 100×. Mirror first.
- **Sub-second OLTP lookups.** Federation latency floor is the source DB's latency floor plus pushdown overhead.
- **High-fan-out joins across many sources.** Each source scanned separately; pulls bytes; falls apart fast.
- **Write-heavy operations.** Most federations are read-only.
- **Workloads sensitive to source-side compute cost.** Federation makes Databricks queries cost on the source.
- **Sources behind unreliable networks.** Network-flakiness becomes your problem.
- **Real-time streams.** Federation is query-on-demand, not streaming.
- **As a permanent data architecture.** Use it to discover/scope/build; mirror or replicate for steady state.
- **Cross-region without willingness to pay egress.** Hidden cost.

## Weaknesses — areas Lakehouse Federation does not do well

- **Pushdown coverage is uneven.** What pushes vs not is per-source and per-operator; surprises happen.
- **Per-user source identity is hard.** Connection identity is the default; per-user requires OAuth federation per source.
- **Schema refresh varies by mode.** Catalog Federation auto-refreshes at query time; Query Federation (JDBC) requires manual catalog refresh on source schema changes.
- **Limited writes.** Read-only on most sources.
- **Result caching weak.** Federation results aren't natively cached for reuse.
- **Lineage stops at the foreign edge.** UC sees "queried foreign table"; doesn't see source-side lineage.
- **Type fidelity issues.** Vendor-specific types (Snowflake VARIANT, BQ STRUCT) need coercion.
- **Cost attribution split.** Databricks side known via system tables; source side via the vendor's billing system.
- **Source-side audit shows one user.** Connection identity, not query author.
- **Throughput governed by source.** A slow Postgres is a slow federated query.
- **Some sources need extra setup.** SSL certs, custom ports, host allow-lists.
- **No first-class join-pushdown across sources.** By design.

---

## Sources

- [Lakehouse Federation docs (Query Federation + Catalog Federation)](https://docs.databricks.com/en/query-federation/index.html)
- [Catalog Federation](https://docs.databricks.com/en/query-federation/catalog-federation.html)
- [CREATE CONNECTION reference](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-create-connection.html)
- [Query pushdown internals](https://docs.databricks.com/aws/en/query-federation/configure-pushdown)
- [Glossary](../Glossary/glossary.md)
