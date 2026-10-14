# Lakeflow Spark Declarative Pipelines (LDP)

> Formerly **Delta Live Tables (DLT)**. Same engine, rebranded under the Lakeflow umbrella. Most docs and SQL keywords still say "live" or "DLT".

## Level 100 — What it is

LDP is a **declarative ETL framework** built on top of Spark Structured Streaming and Delta Lake. You declare *what* tables should exist and *how each is derived from the others*; LDP figures out the DAG, the streaming vs. batch refresh logic, the checkpoints, the retries, and the table maintenance. You write SQL or Python; you do not manage clusters, jobs, or watermarks.

Three things to remember at L100:

- **Declarative**: define tables, not pipelines. LDP composes the DAG from your `CREATE STREAMING TABLE` / `@dlt.table` declarations.
- **Streaming + batch unified**: streaming tables (incremental) and materialized views (full refresh) coexist in one pipeline.
- **Production-grade plumbing free**: data quality `EXPECT` clauses, schema evolution, checkpoint management, retries, and `OPTIMIZE`/`VACUUM` all come built-in.

## Level 200 — The product surface

**Two table types** are the core primitives:

- **Streaming Table (ST)** — incrementally updated from a streaming source (Auto Loader, Kafka, Kinesis, a UC change feed, another ST). Append-only by default; CDC-style merges via `AUTO CDC` (formerly `APPLY CHANGES INTO`).
- **Materialized View (MV)** — fully recomputed each refresh from a deterministic query over batch sources. Used for aggregations, joins to dimensions, gold-layer transforms.

**Other surface area:**

- **`AUTO CDC`** (current API; formerly `APPLY CHANGES INTO`, which still works but is no longer recommended) — collapses a CDC change stream onto a streaming table with SCD Type 1 or Type 2 semantics. Also supports **`AUTO CDC FROM SNAPSHOT`** (Python only) for snapshot-comparison-based CDC when a native change feed isn't available.
- **Expectations** — declarative data-quality rules: `EXPECT col > 0` with action `ON VIOLATION` of `WARN` (record), `DROP` (filter row), or `FAIL UPDATE` (abort).
- **Two languages**: SQL (declarative `CREATE STREAMING TABLE ... AS SELECT ...`) and Python (`@dlt.table`, `@dlt.view`, `dlt.apply_changes(...)`).
- **Two modes**:
  - **Triggered** — runs to completion then stops. Best for scheduled batch (hourly/daily).
  - **Continuous** — long-running streaming. Sub-minute latency on each ST.
- **Two compute flavors**: **Classic** (cluster you size) and **Serverless** (Databricks-managed; recommended). Serverless adds incremental MV refresh and faster autoscaling.
- **Pipelines are UC-native**: source data, target tables, lineage, and pipeline runs all surface in UC. Lineage is computed automatically from the declared DAG.
- **Authored** in: notebook files, `.sql`/`.py` modules in Workspace or Git, deployed via UI or **Databricks Asset Bundles**.

## Level 300 — Architecture

```
Source(s) ──► [LDP Compile DAG] ──► [LDP Runtime on (serverless|classic) compute] ──► UC Streaming Tables / MVs
              ▲                       │
              │                       ├─ per-flow checkpoints (managed)
              │                       ├─ expectations engine (filter/warn/fail)
              │                       ├─ APPLY CHANGES merge engine
              │                       └─ table maintenance (OPTIMIZE/VACUUM)
       Your SQL/Python notebooks
       (CREATE STREAMING TABLE, @dlt.table)
```

When a pipeline update starts, LDP:

1. **Parses** every source file, resolves all `LIVE.<name>` / `dlt.read(...)` references, and builds the DAG.
2. **Validates**: cycles, missing tables, schema compatibility, source/sink permissions.
3. **Plans** each flow as either a streaming query (ST source) or a batch query (MV source). Streaming queries get a managed checkpoint location under the pipeline's storage root.
4. **Runs flows** in dependency order. Independent flows run in parallel. Streaming flows trigger micro-batches under a single Spark application.
5. **Applies expectations** at materialization time. Drop violations filter rows out; warn violations are logged to `event_log`; fail violations abort the update and the pipeline retries.
6. **Maintains** tables: serverless does this automatically; classic runs a periodic maintenance task that performs `OPTIMIZE` / `VACUUM` / liquid-cluster compactions.

**Compute model**:
- **Classic** uses a long-lived DLT-flavoured cluster you size. Autoscaling is "enhanced autoscaling" — DLT releases workers proactively when a flow finishes.
- **Serverless** uses Databricks-managed pools. Per-flow autoscale. Adds **incremental MV refresh** (compute only the delta of the MV instead of full recompute) and **stream pipelining** (parallel reads from same source).

**Schema evolution**:
- Streaming tables auto-add new columns from upstream Auto Loader / CDF sources (when the source supports it).
- Type changes on streaming tables require pipeline `FULL REFRESH`.
- MV schema is derived from the query; column changes are automatic at next refresh.

**UC integration**: targets must be UC catalogs/schemas (Hive Metastore was supported for compatibility but deprecated). Permissions, lineage, audit logs, system tables — all native.

## Level 400 — Deep internals

### Pipeline graph + flow concept

- A "flow" is the atomic unit of work: one writer into one target table. A single table can have multiple flows (e.g., `APPLY CHANGES INTO` + a separate `APPEND FLOW`).
- The DAG is reconstructed on every update by re-parsing source files. There is no persistent schema for the DAG — it is materialised at update time.
- Flow execution proceeds in **topological order**, with independent subtrees running concurrently. The scheduler enforces this even on serverless.

### Checkpoint management

- Each streaming flow owns a checkpoint directory under `<pipeline_storage>/checkpoints/<flow_id>/`. You do not manage these.
- Checkpoints survive code edits as long as the *target table identity* (catalog.schema.name) is unchanged. Renaming a target detaches its checkpoint — equivalent to a full refresh.
- `FULL REFRESH` deletes the checkpoint and (for streaming tables) truncates the target before reprocessing from source.
- Watermarks: declared via `withWatermark()` in Python or `WATERMARK col DELAY OF '...'` in SQL. State retention follows watermark + delay-of.

### Expectations engine

- Three action modes: `ON VIOLATION DROP ROW`, `WARN` (default), `FAIL UPDATE`.
- Expectation evaluation happens in a Spark expression appended to the flow's writer plan; the row count and violation count are emitted to the `event_log` view per micro-batch.
- `FAIL UPDATE` triggers update abort + automatic retry (up to a retry cap). Repeated failures park the pipeline in `FAILED` state.

### AUTO CDC mechanics (formerly `APPLY CHANGES INTO`)

The `AUTO CDC` API is the current recommended interface; `APPLY CHANGES INTO` uses identical syntax and still works but is no longer the recommended form. Lakeflow Connect-generated pipelines use AUTO CDC internally.

- Compiles to an internal merge plan that consumes the source change feed, deduplicates by `KEYS (...)` and `SEQUENCE BY <col>`, then applies inserts/updates/deletes to the target.
- For SCD Type 2, adds the `__START_AT` / `__END_AT` columns and back-dates the prior current row at change time.
- Idempotent on (key, sequence) — replaying a checkpoint replays merges that no-op.
- Out-of-order events: tolerated within the watermark window if declared; outside it, late events may be dropped or land as new rows depending on the source key/sequence.
- **Bitemporal tracking** (Beta): extends SCD Type 2 to track changes across both *business time* and *system time*, enabling point-in-time reconstruction along either dimension.
- **Partial updates**: change records can update only a subset of columns rather than requiring a complete row image.
- **Version functions**: explicit control over snapshot ordering, enabling correct handling of out-of-order or simultaneous arrivals.
- **`AUTO CDC FROM SNAPSHOT`** (Python only): instead of consuming a change feed, compares sequential full snapshots to infer inserts/updates/deletes. Useful when no CDC mechanism is available on the source.
- Note: Expectations (`EXPECT` / `FAIL UPDATE`) are **not supported** with `AUTO CDC FROM SNAPSHOT`.

### Materialized view refresh

- **Classic / non-serverless**: full recompute every update. Expensive for wide aggregations over big tables.
- **Serverless**: LDP analyses the query and, when possible, performs an **incremental MV refresh** — computing only the delta of input changes since the last refresh and merging into the MV. Falls back to full refresh when the query isn't incrementally maintainable (non-deterministic UDFs, certain joins, certain windows). The list of supported operators is documented and growing.

### Event log

- Every pipeline emits a structured `event_log` view (UC table) with one row per significant event: flow started, micro-batch metrics, expectation violations, schema changes, autoscaling events, table maintenance.
- This is the main observability primitive. Build alerts and dashboards off `event_log` rather than scraping driver logs.

### Maintenance

- Both compute flavors run periodic table maintenance: `OPTIMIZE` for small-file compaction, `VACUUM` per retention, liquid-clustering optimisation, predictive-optimization-driven analysis.
- Serverless leans on **Predictive Optimization** for managed tables — runs OPTIMIZE/VACUUM when worthwhile, not on a fixed cadence.

### Limits and operational realities

- **One pipeline ≠ one Spark application** in serverless: multiple flows can run in independent Spark contexts. Don't rely on shared in-memory state.
- A pipeline owns its target tables. If you point another pipeline at the same table, you'll get conflicts. There is a `@dlt.append_flow` decorator to *add* a second flow to a table from the *same* pipeline.
- Python UDFs run on the cluster; serverless restricts Python library installation to the `%pip install` cell at notebook top. No init scripts on serverless.
- `pipelines.notifications` settings can wire Slack/email alerts off pipeline state changes.
- Streaming tables in DBSQL (declared via `CREATE STREAMING TABLE ... AS` in a SQL editor) are backed by hidden LDP pipelines you can inspect.

### Mental model

LDP is **"Spark Structured Streaming + Delta + a DAG planner + a maintenance scheduler"**, all hidden behind declarative table definitions. The thing it owns that you can't easily replicate by hand is the **per-flow checkpoint + expectation + maintenance lifecycle, automatically**, plus the planner that decides when to incrementally refresh an MV. Everything else you could in principle write yourself in Spark.

---

## Anti-patterns — where you should not use LDP

- **Arbitrary Python jobs.** LDP is for declared *tables* derived from other tables. If your work is "read this, hit an external API, write a CSV", use a notebook task in **Lakeflow Jobs** instead.
- **One-shot transforms.** Spinning up an LDP pipeline for a single `CREATE TABLE AS SELECT` is overkill. Use a SQL task or a notebook.
- **Heavily imperative logic.** Loops, branches, retries based on external state, dynamic table names — LDP wants you to declare a static DAG. If you need control flow, drive LDP from a parent Job, or use plain Spark.
- **Non-Delta sinks.** LDP writes UC streaming tables and MVs. If you must write to Snowflake, BigQuery, Kafka, an HTTP API — use Structured Streaming directly with the appropriate sink connector.
- **Hot-path inference / sub-second pipelines.** LDP's smallest unit is a micro-batch (tens of seconds floor in continuous mode). For sub-second event handling, use Structured Streaming directly with foreachBatch, or Zerobus + custom code.
- **Cross-pipeline writes to the same table.** Two pipelines writing the same target is a footgun — they will race on the checkpoint and produce inconsistent state. Use one pipeline with `@dlt.append_flow` for multiple sources into one table.
- **Notebook-driven exploration.** LDP runs are pipeline updates, not interactive cells. Develop and explore in a regular notebook, then port to LDP when you're ready to productionise.
- **You need to call out to external Python libraries on serverless.** Serverless LDP restricts library installation. If you need conda/init scripts or heavyweight ML libs at runtime, classic LDP or a regular Job is the right tool.
- **You want hand-tuned cluster sizing per flow.** LDP autoscales the pipeline as a whole; you can't pin specific worker counts per flow.
- **Pure streaming consumers (e.g., write events to a Kafka topic).** LDP wants the data to land in Delta. If your only goal is forwarding events, use Structured Streaming directly.

## Weaknesses — areas LDP does not do well

- **Dev loop friction.** Each iteration requires "Start" → wait for cluster (classic) or warm-up (serverless) → wait for DAG validation → wait for flow execution. Compared to running a notebook cell, edit-test is much slower. Workarounds: dev-mode pipelines, `pipelines.development = true`, smaller dev datasets.
- **Debugging opacity.** When a flow fails, the surface area is: event log row, driver log buried in the cluster, and (often) a generic Py4J / Spark exception. Stack traces don't always pinpoint your line. Workaround: small flows, lots of expectations.
- **Schema evolution gaps.** New columns: yes. Renames: no. Type widening: limited. Type changes: require `FULL REFRESH` of the affected table.
- **MV incremental refresh isn't universal.** On serverless, many queries fall back to full recompute. The supported-operator list is documented but not exhaustive. Plan for both worlds.
- **Cost surprises.** Continuous pipelines run 24/7. Even on serverless, a low-throughput pipeline left in continuous mode burns DBUs. Triggered + scheduled is usually cheaper.
- **Cross-pipeline coordination.** No first-class way to chain pipelines; you orchestrate them from a parent **Lakeflow Job**. Lineage across pipelines works in UC but pipeline-to-pipeline scheduling is a Job's job.
- **Local testing is awkward.** No `dlt` local executor — you can unit-test pure transformation functions, but the pipeline orchestration only runs on Databricks.
- **Streaming source breadth.** First-class: Auto Loader, Kafka, Kinesis, EventHubs, UC CDF, another streaming table. Anything else needs a custom Spark source or a workaround.
- **`AUTO CDC` needs a sequence column.** If your CDC source doesn't expose a strictly monotonic sequence (some change-tracking sources), you have to manufacture one or use SCD Type 1 with weaker guarantees. `AUTO CDC FROM SNAPSHOT` (Python only) works without a change feed but forfeits expectations support.
- **No mid-flow row-level transforms via UDFs without performance cost.** Python UDFs run row-at-a-time on the JVM/Python boundary; for heavy transforms, write SQL or use pandas UDFs.
- **Schema-of-schema-of-schema.** Pipeline settings, table properties, flow properties, cluster policies — there are several layers and discovering which one is overriding which is annoying.

---

## Sources

- [Lakeflow Spark Declarative Pipelines docs](https://docs.databricks.com/aws/en/dlt/)
- [AUTO CDC reference (current API, replaces APPLY CHANGES INTO)](https://docs.databricks.com/aws/en/dlt/cdc)
- [Expectations docs](https://docs.databricks.com/aws/en/dlt/expectations)
- [Serverless LDP](https://docs.databricks.com/aws/en/dlt/serverless-dlt)
- [Lakeflow umbrella](https://www.databricks.com/product/data-engineering/lakeflow)
- [Glossary](../Glossary/glossary.md)
