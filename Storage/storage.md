# Storage on Databricks

> Cross-cutting topic doc. Synthesizes [[glossary:Delta Lake]], [[glossary:Unity Catalog]], [[glossary:UC Volume]], cloud object storage, and the optimization machinery that sits on top.

## Level 100 — What it is

Storage on Databricks is **cloud object storage (S3 / ADLS Gen2 / GCS) governed by Unity Catalog and accessed almost always through Delta Lake**. There is no Databricks-owned disk array. Your bytes live in your cloud account (or the metastore's managed root) as Parquet files; Delta's transaction log makes them transactional; Unity Catalog decides who can read or write them.

- **Bring your own bucket** — files sit on cloud object storage; Databricks never holds your data on local disk for long.
- **Delta is the default** — every table is a [[glossary:Delta Lake]] table unless you opt out. Open Parquet underneath.
- **Governance is centralized in UC** — one metastore per region, namespaces are `catalog.schema.object`, permissions and lineage are uniform.

## Level 200 — The product surface

Three storage abstractions you actually create:

| Abstraction | What it stores | Access path |
|---|---|---|
| **Table** (managed or external) | Delta data + log | `catalog.schema.table` |
| **Volume** (managed or external) | Arbitrary files (PDFs, images, models, raw drops) | `/Volumes/catalog/schema/volume/...` |
| **Model / function / vector index** | Specialised assets in UC | `catalog.schema.<name>` |

And three plumbing objects in UC that connect them to your cloud:

- **[[glossary:Storage credential]]** — wraps an IAM role / managed identity / GCP SA.
- **[[glossary:External location]]** — `(storage credential) + (cloud URI prefix)`. Grants tables/volumes the right to land there.
- **Managed storage root** — the metastore-default location for managed tables/volumes when no external location is specified.

Table flavors:

- **Managed table** — UC owns the lifecycle of the files; DROP deletes data.
- **External table** — UC points at a path you already control; DROP only removes metadata.
- **Streaming table** / **materialized view** — Delta tables produced by [[glossary:Lakeflow Spark Declarative Pipelines]]; refresh semantics are pipeline-managed.
- **Foreign table** — points into [[glossary:Lakehouse Federation]]; data stays in Snowflake/Postgres/etc.
- **Delta Sharing share / recipient table** — read-only Delta-protocol table from another org or another metastore.

Volumes:

- **Managed volume** — files under the metastore-managed root, governed like a managed table.
- **External volume** — UC-governed view onto an existing path you own.

Write APIs land in the same place: SQL (`INSERT`, `MERGE`, `COPY INTO`, `CREATE TABLE … LOCATION …`), DataFrame (`.write.format("delta").saveAsTable(...)`), Auto Loader for incremental ingest, Lakeflow Connect for managed source-to-Delta.

## Level 300 — Architecture

```
              ┌───────────────────────────── Unity Catalog ─────────────────────────────┐
              │  Metastore (1 per region)                                                │
              │   └── Catalog                                                            │
              │        └── Schema                                                        │
              │             ├── Tables    (managed | external | streaming | foreign)    │
              │             ├── Volumes   (managed | external)                           │
              │             ├── Models / Functions / Vector indexes                      │
              │             └── External locations  → Storage credentials                │
              └────────────────────┬───────────────────────────────┬─────────────────────┘
                                   │                               │
                  Permission check │                  Path → cred  │
                                   ▼                               ▼
                ┌──────────────── Compute (Spark/Photon, SQL Warehouse, Serverless) ───────────────┐
                │  reads _delta_log (JSON+checkpoint) → prunes files via stats → opens Parquet     │
                │  applies row filters / column masks / deletion vectors                            │
                └──────────────────────────────┬───────────────────────────────────────────────────┘
                                               ▼
                                    ┌──────────────────────┐
                                    │  Cloud object store  │   (S3 / ADLS Gen2 / GCS)
                                    │   ├── _delta_log/    │
                                    │   ├── part-*.parquet │
                                    │   └── _change_data/  │
                                    └──────────────────────┘
```

**Three layers, top down:**

1. **Logical (UC)** — names, permissions, lineage, audit. The catalog is the API surface; everything else is plumbing.
2. **Format (Delta)** — `_delta_log/` is the database. Each commit is a JSON file of actions (`Add`, `Remove`, `Metadata`, `Protocol`, `Txn`, `CommitInfo`). Checkpoints every N commits. Stats inside `Add` actions power file pruning at read time. See [[glossary:Delta Lake]].
3. **Physical (cloud storage)** — Parquet files, deletion-vector files, `_change_data/` if CDF is on. Storage credentials gate the IAM call; external locations gate the *path prefix*.

**Read path** (any engine): UC checks permission → returns a short-lived credential → engine reads `_last_checkpoint` and the JSON tail → builds the snapshot → prunes files via stats and partition/cluster predicates → opens Parquet → applies deletion vectors and any row filters / column masks.

**Write path**: same auth, then **optimistic concurrency** — read current version `v`, plan actions, attempt to commit at `v+1`. If the commit file already exists, retry against the new snapshot. Conflicts are decided on overlapping files, not row keys.

**Maintenance loop**:
- [[glossary:OPTIMIZE]] compacts small files.
- Liquid clustering routes new writes by hash of cluster columns; OPTIMIZE merges clusters.
- [[glossary:Deletion vector]] files soft-delete rows; periodic OPTIMIZE rewrites them away.
- [[glossary:VACUUM]] physically deletes tombstoned files past retention (default 7 days).
- [[glossary:Predictive optimization]] (UC managed only) runs OPTIMIZE / VACUUM / ANALYZE automatically when the math says it pays.

**Governance overlay**: UC injects row filters and column masks into the logical plan before files are read — engines can't bypass them. Audit and lineage are recorded in `system.*` tables.

## Level 400 — Deep internals

### Cloud object store characteristics that actually matter

- **PUT-then-LIST is eventually consistent on some legacy paths** — modern S3 / ADLS Gen2 / GCS are read-after-write consistent, which is what Delta's commit protocol depends on. ADLS Gen2 hierarchical namespace gives directory rename atomicity; flat-namespace S3 does not, which is why Delta avoids relying on renames.
- **Commit atomicity** on S3 is guaranteed by **conditional PUT** to the next `*.json` log file (if-none-match). The first writer wins; the loser sees `412 Precondition Failed` and retries. Older S3 paths used DynamoDB-based log stores; modern Databricks uses conditional writes natively.
- **Throughput** is per-prefix on S3 (~3,500 PUT / 5,500 GET per prefix per second baseline). Many small writers hammering the same `_delta_log/` prefix can become the bottleneck on extremely write-heavy tables — partitioning the workload across tables, not over-using a single hot table, is the fix.
- **Storage class** matters: Standard for hot Delta tables, S3 Intelligent-Tiering or equivalent for cold history. Archive tiers (Glacier / Archive) break Delta — files become inaccessible without restore.

### The transaction log, again, but for real

- A commit is `00000000000000000XXX.json`. Each line is one JSON action. Order within the file matters for conflict resolution but not for the resulting snapshot.
- **Checkpoints** (`*.checkpoint.parquet`) materialise the snapshot every `delta.checkpointInterval` commits (default 10). Without checkpoints, readers would replay every commit since v0.
- **Multi-part v2 checkpoints** split a single logical checkpoint across multiple Parquet files for huge tables (millions of files). Required for tables with very high file counts to keep checkpoint reads fast.
- **`_last_checkpoint`** is a single small JSON file pointing readers at the latest checkpoint version. If lost, readers fall back to scanning the log.
- **Protocol versions** (`minReaderVersion`, `minWriterVersion`) gate features. Enabling [[glossary:Deletion vector]] / [[glossary:Column mapping]] / type widening bumps the protocol. Older clients refuse the table.
- **Idempotent writes** via `Txn` actions: `(appId, version)` lets a streaming job recommit safely on retry without duplicating data.

### Concurrency, conflicts, and isolation

- Default isolation is **WriteSerializable** for Delta tables (the strongest practical level). **Serializable** is opt-in via `delta.isolationLevel` and adds full snapshot-isolation checks on reads inside a transaction.
- Conflicts are **file-level**: two writers that don't touch the same files both commit. Two `MERGE`s on overlapping partitions collide and one retries.
- **MERGE** is the contention magnet. Mitigations: declare cluster columns so MERGE rewrites are scoped; enable deletion vectors so DELETE/UPDATE/MERGE don't rewrite whole files; partition the workload so writers target disjoint files.
- Streaming writers commit on micro-batch boundaries — too aggressive a trigger (sub-second) creates pathological log growth. Trigger.AvailableNow or 30s–5min triggers are typical.

### File layout: partitioning, clustering, file sizing

- **Liquid clustering** (`CLUSTER BY`) is the modern default. The cluster columns hash into clusters; writes route to clusters; OPTIMIZE merges clusters incrementally. Adding/removing cluster columns is a metadata-only change. GA for Delta tables (DBR 15.4 LTS+); public preview for Iceberg tables (DBR 16.4 LTS+).
- **Hierarchical clustering** (DBR 17.1+) — specify columns in priority order; lower-cardinality columns first maximize data skipping across the hierarchy.
- **Partial reclustering with predicates** (DBR 18.1+) — `OPTIMIZE tbl FULL WHERE <predicate>` targets a subset of the table, useful for backfilling recent partitions without reclustering the full table.
- **`PARTITIONED BY`** is legacy for most workloads. Still appropriate when:
  - Partition column has bounded low cardinality (region, date-by-day).
  - You need partition-level operations (DROP PARTITION, REPLACE).
  - Engine compatibility requires it (some external Iceberg readers).
- **Target file size**: 128 MB–1 GB depending on workload. `OPTIMIZE` defaults to ~1 GB; smaller (256 MB) helps interactive workloads with high selectivity.
- **Stats footprint**: `delta.dataSkippingNumIndexedCols` (default 32) controls how many leading columns get min/max stats. Wide tables should reorder so hot predicate columns are first, or raise the limit.

### Deletion vectors

- A DV is a Roaring-bitmap file referenced by `Add.deletionVector` (path, offset, length, cardinality).
- Mark-on-read: scanners load the bitmap and skip those row positions in the Parquet file.
- Photon and modern Spark apply DVs natively; some external readers do not — UniForm Iceberg metadata reflects DVs as positional deletes for Iceberg-compatible engines.
- DV creates write amplification on the log (every DELETE adds a DV file) but avoids file rewrites — net win for sparse deletes, net loss for heavy churn (re-OPTIMIZE periodically).
- `REORG TABLE tbl APPLY (PURGE)` physically rewrites files that contain DV-flagged rows, removing the deleted rows from Parquet; set `spark.databricks.delta.reorg.purgeMode = rows` for more targeted large-table purges.
- On Iceberg v3 tables, deletion vectors are included by default.

### Column mapping & type widening

- **Column mapping** decouples logical column name from physical Parquet field id (id mode) or physical name (name mode). Required for `RENAME COLUMN`, `DROP COLUMN`. Enabling it bumps `minReaderVersion`.
- **Type widening** lets you widen integer/decimal types in-place (e.g., INT → BIGINT) without rewrite. Narrowing still requires rewrite. Partial coverage — check protocol notes per release.

### Change Data Feed (CDF)

- Opt-in per table: `delta.enableChangeDataFeed = true`.
- Every write emits per-row events into `_change_data/` with `_change_type` (`insert` | `update_preimage` | `update_postimage` | `delete`).
- Read via `SELECT * FROM table_changes('cat.sch.tbl', startVersion, endVersion)`.
- Underpins [[glossary:APPLY CHANGES INTO]] and incremental silver/gold pipelines.

### Predictive Optimization

- Runs only on UC **managed** tables (Delta and Iceberg formats). External tables, OpenSharing recipient tables, and HMS tables are out of scope.
- Service decides when to OPTIMIZE / VACUUM / **ANALYZE** (stats collection) based on small-file count, write rate, and query patterns. Bills usage against your account but typically beats naive nightly cron because it skips no-op runs and respects active queries.
- Enabled by default for accounts created on or after November 11, 2024; existing accounts being gradually enabled.

### Universal Format (UniForm)

- One Parquet directory, multiple metadata layers: Delta `_delta_log/` plus Iceberg `metadata/` generated asynchronously alongside each Delta commit. (Hudi metadata generation was removed from UniForm; Iceberg is the only supported external format.)
- Lets Snowflake / Trino / BigQuery / Athena / Redshift (via their Iceberg readers) read your Delta tables without copy.
- OpenSharing recipients can also read UniForm-enabled tables as Iceberg via the Iceberg REST Catalog API (public preview).
- Costs: small per-commit metadata write; some Delta features (e.g., DV with specific configs, column-mapping name mode) limit what non-Delta readers see — they may see deleted rows or fail to resolve renames.

### Volumes: the non-tabular escape hatch

- Path scheme `/Volumes/<catalog>/<schema>/<volume>/...`. Same permission model as tables.
- Managed volumes live under the metastore's managed root; external volumes are governed views onto an external location.
- Use volumes for: model artifacts, PDFs/images feeding RAG, raw drops before Auto Loader, init scripts, library jars.
- **Do not** use volumes as a replacement for tables — there's no transactional guarantee on volume writes and no stats-based pruning.

### Cross-region and cross-cloud

- Storage and metastore are **regional**. A workspace in us-east-1 reads us-east-1 storage cheaply; reading us-west-2 storage incurs egress.
- Delta Sharing is the right primitive for cross-region or cross-org access — no copy, recipient reads via the Delta Sharing protocol.
- True multi-region active-active is not a Databricks-native concept — you replicate either via deep clones into the second region or via Delta Sharing reads.

### Hard limits to keep in your head

- ~10⁹ files per table is a soft ceiling — multi-part checkpoints help, but query planning latency grows.
- `_delta_log/` JSON files older than the latest checkpoint can be cleaned by `delta.logRetentionDuration` (default 30 days), but you lose time-travel beyond that.
- `VACUUM` retention default 7 days exists to protect in-flight readers and time-travel. Lowering it below 7 days requires `spark.databricks.delta.retentionDurationCheck.enabled = false` — a footgun.
- Storage credential token TTL is short (minutes). Long-running jobs refresh via UC automatically; raw `boto3` from a notebook doesn't.

### Mental model

Storage on Databricks is **"cloud bucket + Delta log + UC permissions, in that order of importance."** The bucket holds bytes. The log makes the bytes transactional. UC decides who can see the log. Everything else — clustering, DVs, CDF, UniForm, predictive optimization — is sugar on top of those three layers. Master the layering and you can debug any storage problem in three jumps: *is it a permissions issue (UC) → a log/snapshot issue (Delta) → or a cloud-storage issue (S3/ADLS/GCS)?*

---

## Anti-patterns — where you should not use Databricks storage like this

- **Storing OLTP working sets in Delta.** No per-row locks, no sub-ms reads. Use [[glossary:Lakebase]] for transactional workloads, sync to Delta for analytics.
- **High-cardinality `PARTITIONED BY`** (user_id, timestamp-to-the-second). Classic millions-of-tiny-files disaster. Liquid clustering.
- **Tables on archive storage tiers** (S3 Glacier, ADLS Archive). Delta needs random reads on `_delta_log` — archive tiers break the table.
- **Treating volumes as tables.** Volumes are file storage. No ACID, no stats. If you find yourself doing `pandas.read_csv(volume_path)` in a job hot path, the data belongs in a table.
- **Manual file writes into a Delta table directory** (e.g., `aws s3 cp ... s3://table-root/`). You bypass the log; readers ignore the file or worse, the table corrupts.
- **One Delta table per tenant in a multi-tenant SaaS.** Thousands of tables per schema is awkward to govern. Multi-tenant + row filters in UC is usually the right model.
- **Hive metastore for new work.** [[glossary:Hive Metastore (HMS)]] is legacy. No UC features (lineage, ABAC, masks, predictive optimization). Migrate.
- **Storing large blobs as columns** (multi-MB strings, encoded images). Inflates Parquet, kills stats. Volumes + path reference.
- **Using time travel as backup.** Bounded by retention; not designed for DR. Deep clone or external backup.
- **Cross-region writes to a single hot table.** Every commit is a round-trip; latency stacks up. Co-locate writers and storage.

## Weaknesses — areas storage on Databricks does not do well

- **Sub-second write latency** isn't a goal. Even Zerobus's "near real-time" path is a few seconds end-to-end. Hard real-time wants Kafka + a streaming sink, not Delta as primary store.
- **Per-row reads on huge tables** without good clustering are slow — Delta is built for scans, not point lookups. Online tables / Lakebase exist for a reason.
- **Schema drift across many writers** is brittle. Auto Loader's `cloudFiles.schemaEvolutionMode` helps for files; concurrent writers with divergent schemas still conflict.
- **Cross-format interop with full feature parity is hard.** UniForm gets you Iceberg/Hudi reads but pinning down which Delta features are visible to which external reader is an ops job.
- **Permission propagation latency**: UC permission changes can take a few seconds to a minute to reach all compute. Plan for the eventual-consistency window.
- **External tables outside Predictive Optimization.** No automatic OPTIMIZE/VACUUM. They bloat without an owner.
- **Deletion vectors are not universally supported.** Pre-V3 readers may miss DVs and return deleted rows. Mitigate via UniForm or scheduled rewrite OPTIMIZE.
- **Storage credential debugging is opaque.** When a permission check fails, error messages bury the cloud-IAM reason. `system.access.audit` helps but isn't real-time.
- **Multi-region active-active doesn't exist as a managed product.** You roll your own with deep clones or Delta Sharing.
- **Big checkpoints** for tables with hundreds of millions of files slow planning; you must opt into v2 multi-part checkpoints carefully.
- **Volumes have no stats and no clustering.** Lots of small files in a volume + an Auto Loader stream is fine; querying volumes directly with SQL is not.
- **VACUUM is a foot-cannon.** Setting `delta.deletedFileRetentionDuration` to '0 hours' then VACUUM-ing destroys time travel and any in-flight read.

---

## Sources

- [Delta Lake docs (Databricks)](https://docs.databricks.com/aws/en/delta/)
- [Delta protocol spec (open source)](https://github.com/delta-io/delta/blob/master/PROTOCOL.md)
- [Unity Catalog](https://docs.databricks.com/aws/en/data-governance/unity-catalog/)
- [Managed vs external tables](https://docs.databricks.com/aws/en/tables/managed)
- [UC Volumes](https://docs.databricks.com/aws/en/volumes/)
- [External locations & storage credentials](https://docs.databricks.com/aws/en/connect/unity-catalog/external-locations)
- [Liquid clustering](https://docs.databricks.com/aws/en/delta/clustering)
- [Deletion vectors](https://docs.databricks.com/aws/en/delta/deletion-vectors)
- [Predictive Optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
- [UniForm](https://docs.databricks.com/aws/en/delta/uniform)
- [Delta Sharing](https://docs.databricks.com/aws/en/delta-sharing/)
- Companion product docs: [Delta Lake](../DeltaLake/delta-lake.md), [UC Volumes](../UCVolumes/uc-volumes.md), [Unity Catalog](../UnityCatalog/unity-catalog.md), [Auto Loader](../AutoLoader/auto-loader.md), [Delta Sharing](../DeltaSharing/delta-sharing.md)
- [DBR 18 LTS release notes](https://docs.databricks.com/aws/en/release-notes/runtime/18.html)
- [Glossary](../Glossary/glossary.md)
