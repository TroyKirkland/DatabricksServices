# Databricks Lakeflow Connect

## Level 100 — What it is

Lakeflow Connect is Databricks' **managed ingestion** offering. You point it at a source (a SaaS app, an operational database, files in cloud storage), and it lands the data into **Unity Catalog–governed streaming tables** on **Delta**, on a schedule. No clusters to size, no Python to write, no Fivetran-style separate vendor.

Three things to remember at L100:

- **Managed**: connectors are first-party, fully hosted by Databricks.
- **Incremental by default**: first run snapshots, subsequent runs only pull what changed.
- **Lakehouse-native**: output is UC streaming tables; downstream Lakeflow Spark Declarative Pipelines (LDP/DLT), SQL, AI/BI, and ML can use them directly.

## Level 200 — The product surface

**Connector families:**

- **SaaS connectors** (API-driven): Salesforce, Workday, ServiceNow, HubSpot, Jira, Google Analytics, NetSuite, etc.
- **Database connectors** (CDC-driven): SQL Server, PostgreSQL, MySQL, Oracle.
- **File source connectors**: Google Drive, SharePoint, and enterprise file storage. These do not use CDC; they ingest files directly into streaming tables.
- **Query-based connectors**: direct database querying without CDC configuration; no gateway or change log required. Useful for sources where CDC is unavailable or unnecessary.
- **Streaming connectors**: RabbitMQ and other event streaming sources.
- **Community connectors**: open-source, community-built options for sources not covered by managed connectors.

**Core objects you create:**

1. **Connection** — UC securable holding credentials/host/auth (OAuth U2M, OAuth M2M, or basic).
2. **Ingestion pipeline** — the actual job that pulls from the source and writes streaming tables. Built on Lakeflow Spark Declarative Pipelines.
3. **(For DB connectors) Ingestion gateway** — a separate pipeline that continuously reads the source's change log and stages it.
4. **Destination streaming tables** — Delta tables in UC. CDC sources land via `APPLY CHANGES INTO`, so deletes/updates merge correctly.
5. **Schedule** — a Lakeflow Job is auto-generated to run the pipeline on cadence (or continuously).

You author this in the UI, via API, or as Databricks Asset Bundles for CI/CD.

## Level 300 — Architecture

**Two-pipeline pattern for database CDC sources:**

```
Source DB ──► Ingestion Gateway ──► UC Staging Volume ──► Ingestion Pipeline ──► Streaming Tables
            (classic compute)       (Avro/JSON files)      (serverless LDP)        (UC, Delta)
```

- **Gateway pipeline** runs on **classic compute** inside your workspace VPC/VNet. It needs network reachability to the source (VPN, Direct Connect/ExpressRoute, peering, Private Link, or public endpoint). It does the snapshot read **and** the continuous change-log tail in parallel.
- **Staging UC Volume** is a Databricks-managed volume in a catalog/schema you specify; the gateway writes raw extracted change files there. Cloud-storage-encrypted at rest.
- **Ingestion pipeline** runs on **serverless LDP** compute. It reads from the staging volume and `APPLY CHANGES INTO` the destination streaming tables, handling inserts/updates/deletes and schema evolution. Optional SCD Type 2.

**Why split?** Decoupling lets the gateway maintain a single, stable, long-lived connection to the source (which is what most operational DBs want) while the apply side scales elastically on serverless and can be backfilled/replayed from the staging volume without re-hitting the source.

**SaaS connectors** skip the gateway — the pipeline directly calls the source's REST API and writes to streaming tables, using cursor columns or a source-provided change feed.

**CDC mechanism varies by source:**

- **SQL Server**: prefers **Change Tracking** if a primary key exists (lighter source load, row-level only); falls back to **CDC** (heavier, but column-level history, no PK required). If both are enabled, Change Tracking wins.
- **PostgreSQL**: logical replication slots + WAL.
- **MySQL**: binlog (row-based).
- **Oracle**: LogMiner / redo logs.

**Schema evolution**: new columns and new tables are picked up automatically. Data-type changes and column renames are **not** auto-handled — they require pipeline reset for the affected table.

**Governance**: pipelines, gateways, connections, staging volumes, and destination tables are all UC objects. Lineage, audit, and row/column permissions are inherited from UC.

## Level 400 — Deep internals

This is where the architecture earns its keep. Specifics drawn from current docs/blog; behavior can shift with releases.

### Gateway internals

- **Continuously running**, not scheduled. This is a hard requirement: SQL Server CDC tables, PG replication slot positions, and MySQL binlogs are **truncated** after retention windows. If the gateway stops longer than source retention, you must reseed (snapshot) the affected tables.
- The gateway maintains a **source-side log position** (LSN for SQL Server, LSN/confirmed_flush_lsn for PG, binlog file+pos+GTID for MySQL, SCN for Oracle) as its checkpoint. Restart resumes from the last committed position written to the staging volume — exactly-once landing into staging because position commits and file commits are sequenced.
- **Snapshot + CDC parallelism**: at first run, the gateway snapshots each table while *simultaneously* buffering change events. The handoff is reconciled by LSN: the apply pipeline knows the snapshot represents the table at LSN X, then replays change events with LSN > X. This is the same pattern Debezium uses, but managed.
- **Classic compute** is used (not serverless) because the gateway needs:
  - long-lived stateful connections (replication slots, snapshot isolation transactions),
  - vendor JDBC/native client drivers and capture-agent quirks,
  - placement inside *your* VPC/VNet for network reachability to private sources,
  - sustained, predictable IO — not the bursty profile serverless is tuned for.
  - Minimum recommended sizing is ~8 cores; scale up for high-throughput sources.
- Mixed billing follows: **classic DLT DBUs** for the gateway, **serverless DLT DBUs** for the ingestion pipeline.

### Staging volume layout

- A UC Volume (non-tabular storage) sits between gateway and apply. Files are written as serialized change events (Avro/JSON-style payloads with op type, before/after images where available, and the source LSN).
- The volume is the **system of record between the two pipelines** — it's how the apply side can be reset and replayed without touching the source. If you need to fully reset a destination table, you re-snapshot from source; if you need to recompute downstream of staging, you replay from the volume.
- Encrypted by the underlying cloud KMS; lives in *your* storage. Staging catalog/schema can differ from destination.
- Staging data is **automatically purged after 30 days**. The apply-side replay window is bounded by this retention. For audit-grade event history, land changes into a separate Delta CDF table downstream rather than relying on staging replay.

### Apply side (`APPLY CHANGES INTO`)

- The ingestion pipeline is just an LDP pipeline with declared streaming tables and `APPLY CHANGES INTO` declarations against the staged change stream. This is the same primitive available to user pipelines — Lakeflow Connect just generates and manages it for you.
- Default mode is SCD Type 1 (current-state). SCD Type 2 is selectable per table and adds `__START_AT`/`__END_AT` semantics with sequence-by the source LSN, giving strict ordering even with out-of-order delivery from staging.
- The apply side is **idempotent** because `APPLY CHANGES INTO` deduplicates by sequence key. Restarting the ingestion pipeline replays from its DLT checkpoint without producing duplicate rows in the destination.

### Schema evolution mechanics

- New column: appended to the destination streaming table; the row that introduced it has the new column, prior rows are `NULL`. No pipeline restart needed.
- New table: discovered on the next gateway run; if it's included in the table list (including wildcards), it begins snapshotting on the next cycle.
- Dropped column: column persists in destination (Delta column-mapping semantics); new rows have `NULL`.
- **Not supported automatically**: type changes, renames — these surface as pipeline failures requiring a `FULL REFRESH` on the affected table.

### Limits and operational realities

- **250 tables per pipeline** (DB connectors). Beyond that, split into multiple gateway/ingestion pipeline pairs — typical pattern is one pair per logical source database or per workload domain.
- API-based row filtering is not supported for DB connectors at present; filter at the destination view layer or restrict columns/tables instead.
- The gateway is the bottleneck for source-side cost: heavy CDC (vs. Change Tracking) on SQL Server materially increases source DB load. Pick CT when PKs exist.
- Networking: only the gateway needs source reachability. The ingestion pipeline reads from UC storage and never talks to the source. Private Link works for both source-to-gateway and Databricks control-plane-to-data-plane.
- Fault tolerance composes: source log retention bounds gateway downtime tolerance; staging volume retention bounds apply-side replay window; Delta time-travel/Vacuum bounds downstream recovery.

### Mental model

Think of Lakeflow Connect's DB connectors as **a managed Debezium-style log-shipping pipe** where (a) the connector is owned and operated by Databricks, (b) the staging "topic" is a UC Volume instead of Kafka, and (c) the sink is a Delta streaming table consumed by `APPLY CHANGES INTO` rather than a Kafka Connect JDBC sink. The split into two compute pools is the key architectural decision — it trades a bit of complexity for clean separation of "talk to the source" vs. "land into the lakehouse" and lets each side scale and recover independently.

---

## Anti-patterns — where you should not use Lakeflow Connect

These are use cases where the product is the wrong tool; reach for something else.

- **Sub-second / true-real-time pipelines.** Lakeflow Connect is micro-batch with a staging hop. End-to-end latency floor is minutes, not milliseconds. If you need sub-second ingest (fraud scoring, ad bidding, IoT telemetry at event speed), use **Spark Structured Streaming on Kafka/Kinesis/EventHubs**, or **Zerobus** for direct gRPC writes into Delta.
- **Reverse ETL / outbound to SaaS.** It only ingests *into* the lakehouse. To push data back to Salesforce/HubSpot/etc., use a reverse-ETL tool (Hightouch, Census) or a partner integration.
- **One-shot historical migrations.** Spinning up a gateway + ingestion pipeline for a single bulk load is overkill. Use `COPY INTO`, a one-time JDBC read, `lakehouse-federation`, or DBSQL Lakehouse Federation queries instead — they're cheaper and simpler for a one-and-done load.
- **Sources outside the supported connector list.** Don't try to wedge an unsupported SaaS or DB in via custom hacks. Use **Auto Loader** for files, **Spark custom data sources** for unique systems, **Lakehouse Federation** for query-time access, or a community connector. Forcing it leads to brittle pipelines you have to operate yourself.
- **Heavy in-flight transformation, enrichment, or joins.** Lakeflow Connect deliberately lands raw-ish data. Don't expect to do multi-source joins, external API enrichment, or PII tokenization *during* ingest. Do those downstream in **Lakeflow Spark Declarative Pipelines** (bronze → silver → gold), where you have full Spark/SQL.
- **Fine-grained row-level filtering at the source.** API-based row filtering isn't supported for DB connectors. If compliance requires that certain rows *never leave the source*, Lakeflow Connect is the wrong layer — filter inside the source DB via a replication view, or pick a different tool.
- **Sources with extreme schema churn (frequent type changes, renames).** Schema evolution handles adds well, but type changes and renames force `FULL REFRESH`. A source where dev teams rename columns weekly will cause constant re-snapshots — push back on the source team or pick another approach.
- **Cross-region / multi-cloud sources where the gateway can't be near the source.** Gateway runs in *your* workspace VPC/VNet. If the source is in a different cloud or region with no good private path, expect WAN egress costs and reliability headaches.
- **Tiny low-value sources.** Running a 24/7 classic-compute gateway for a database that only changes a few rows per day is a cost anti-pattern. Use a scheduled JDBC read into a notebook/job instead, or batch into a single shared pipeline.
- **Destinations that aren't Delta / UC.** Lakeflow Connect writes UC streaming tables on Delta. If your sink must be a non-Delta format or a non-UC location, use a different ingest tool.

## Weaknesses — areas where it doesn't do well

These are real limitations to design around even when Lakeflow Connect *is* the right choice.

- **Latency floor.** The two-pipeline split (gateway → staging volume → ingestion pipeline → Delta) means even "continuous" mode is minutes-class, not seconds. Don't promise sub-minute freshness SLAs.
- **Gateway is classic compute, must run 24/7.** No serverless gateway today. You pay sustained classic DLT DBUs even when source change volume is low. Right-sizing is your problem; the gateway can't scale to zero.
- **Source-side load is non-trivial.** SQL Server CDC mode in particular adds meaningful load on the source — capture jobs, extra writes to CDC tables. PostgreSQL logical replication pins WAL until the slot is consumed. DBAs need to size for this.
- **250 tables per pipeline ceiling.** Large source databases (think SAP, big ERP schemas) need to be sharded across multiple gateway+ingestion pairs. There's no single-pane management across shards.
- **Schema evolution has hard gaps.** Adds: yes. Drops: tolerated. **Type changes, renames: no** — they fail and require `FULL REFRESH` of the affected table, which re-snapshots from source. For a multi-TB table, that's an outage-class event.
- **Outage recovery is bounded by source log retention.** If the gateway is down longer than SQL Server CDC retention / PG slot retention / MySQL binlog retention, you've lost replay capability and must re-snapshot. Plan retention windows defensively (24–72h is common).
- **Observability spans two pipelines.** Gateway and ingestion pipeline have separate event logs, separate runs, separate failures. Correlating "why didn't this row land" across both is more work than a single-pipeline tool. Lineage is good; root-causing is mediocre.
- **No per-row transform or filter hook during ingest.** You can't run a UDF, hash a column, drop a PII field, or filter rows mid-flight. All cleanup happens downstream.
- **Connector breadth still growing.** Big-name SaaS sources are covered, but the long tail (many marketing, finance, vertical-specific SaaS apps) isn't. Check the supported list before scoping; don't assume.
- **Cost predictability is mediocre.** Serverless DLT pricing on high-volume CDC streams can surprise. The gateway adds a fixed-ish classic DBU floor on top. Model both before committing.
- **Networking for on-prem sources is on you.** Direct Connect / ExpressRoute / Site-to-Site VPN / Private Link setup, firewall rules, and DNS are customer responsibilities. Lakeflow Connect won't tunnel for you.
- **Limited replay/time-travel within staging.** You can replay the apply side from staging, but staging data is automatically purged after 30 days. The 30-day window is the hard bound on the apply-side replay window. For audit-grade event history, land changes into a separate Delta CDF table downstream.

---

## Sources

- [Managed connectors in Lakeflow Connect (AWS docs)](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/)
- [Database connectors (CDC) overview — gateway/staging architecture](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/cdc-overview)
- [Ingest data from SQL Server (AWS docs)](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sql-server)
- [Ingest data from SQL Server — pipeline reference](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sql-server-pipeline)
- [Lakeflow Connect: SQL Server connector blog](https://www.databricks.com/blog/lakeflow-connect-efficient-and-easy-data-ingestion-using-sql-server-connector)
- [Lakeflow Connect product page](https://www.databricks.com/product/data-engineering/lakeflow-connect)
- [What is Lakeflow Connect? (Azure docs)](https://learn.microsoft.com/en-us/azure/databricks/ingestion/overview)
