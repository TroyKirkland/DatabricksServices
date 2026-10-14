# Delta Lake

## Level 100 — What it is

Delta Lake is an **open-source transactional storage format** layered on Parquet. It adds ACID transactions, schema enforcement, time travel, MERGE/UPDATE/DELETE, and metadata-level statistics — without giving up the open file format or requiring a proprietary engine.

- **ACID on object storage**: atomic commits, reader/writer isolation via a transaction log.
- **Open format**: Parquet + a JSON+checkpoint log; readable by any Delta-compatible reader.
- **The substrate of the lakehouse**: every Databricks table you'll meet is Delta unless explicitly otherwise.

## Level 200 — The product surface

- **Table types**: managed (UC owns the storage), external (UC points at an existing location), shallow clone, deep clone, streaming tables, materialized views.
- **Write API**: SQL (`INSERT`, `MERGE`, `UPDATE`, `DELETE`, `COPY INTO`) and DataFrame (`.write.format("delta")` / `.saveAsTable`).
- **Schema enforcement**: writes that don't match the table schema fail unless `mergeSchema` is set; column adds via `ALTER TABLE`.
- **Time travel**: `VERSION AS OF n` / `TIMESTAMP AS OF '...'`. Bounded by `delta.deletedFileRetentionDuration` and `VACUUM`.
- **Change Data Feed (CDF)**: `ALTER TABLE … SET TBLPROPERTIES (delta.enableChangeDataFeed = true)` exposes per-row inserts/updates/deletes for downstream readers via `table_changes(...)`.
- **Table optimisation**:
  - `OPTIMIZE` — compact small files.
  - `ZORDER BY` — legacy multi-dim data skipping (mostly superseded).
  - **Liquid clustering** — declare `CLUSTER BY` columns; Delta clusters incrementally as data arrives.
  - **Deletion vectors** — soft-delete rows without rewriting Parquet.
  - **Predictive Optimization** (UC managed only) — auto-runs OPTIMIZE / VACUUM; enabled by default for accounts created on or after November 2024.
- **Constraints**: `NOT NULL`, `CHECK (expr)` constraints on columns.
- **Column mapping** — rename / drop columns without rewriting files (id mode or name mode).
- **Row tracking** — `delta.enableRowTracking = true`; assigns a stable `_metadata.row_id` and a `_metadata.row_commit_version` to every row for row-level lineage. GA since DBR 14.0.
- **UniForm** — write Iceberg metadata alongside Delta (Iceberg-only; Hudi support was removed) so non-Databricks engines can read the table without copy.

## Level 300 — Architecture

```
Delta table directory
├── _delta_log/
│   ├── 00000000000000000000.json   (commit 0)
│   ├── 00000000000000000001.json   (commit 1)
│   ├── ...
│   ├── 00000000000000000010.checkpoint.parquet   (every N commits)
│   └── _last_checkpoint
├── part-00000-uuid.snappy.parquet
├── part-00001-uuid.snappy.parquet
└── ...
```

**Transaction log = the database**. Each commit is a JSON file containing zero or more *actions*: `Protocol`, `Metadata`, `Add` (new file with stats), `Remove` (tombstone), `Txn` (idempotent app token), `CommitInfo`. The current table state is the cumulative result of all actions up to the latest commit, periodically materialised as a `checkpoint.parquet` to avoid replay.

**Reads** load the latest checkpoint + tail of JSON commits, then prune files using min/max stats and partition predicates.

**Writes** are **optimistic concurrency**: writer reads current version, plans actions, tries to commit at `version+1`. If another writer beat it, it retries by re-reading state.

**Maintenance**:
- `OPTIMIZE` rewrites small files into larger ones; on UC managed tables, Predictive Optimization decides when.
- `VACUUM` deletes files that are no longer referenced and older than retention (default 7 days).
- **Liquid clustering** drives incremental clustering on declared columns during writes and OPTIMIZE.

**Photon**: query engine that understands Delta deeply — reads stats from the log, prunes files, vectorises Parquet decoding, handles deletion vectors transparently.

## Level 400 — Deep internals

### The transaction log in depth

- **Commit files**: `00000000000000000000.json` etc. Each line is a JSON action.
- **Checkpoints** every 10 commits by default (`delta.checkpointInterval`). Two flavors: single-file Parquet (default) and **multi-part v2 checkpoints** (for very large tables).
- **`_last_checkpoint`** points readers at the most recent checkpoint to avoid scanning all log files.
- **Protocol versions** are versioned: a reader's required protocol must be ≥ the table's `minReaderVersion` (same for writer). Some features (deletion vectors, column mapping, type widening) bump the protocol.
- **Idempotent writes**: `txnAppId + txnVersion` actions let a streaming writer commit at-most-once even on retry.

### Add / Remove / Stats

- An `Add` action records `path`, `partitionValues`, `size`, `dataChange`, `tags`, and crucially `stats` — a JSON blob with min/max/null-count per column. Stats power query pruning.
- A `Remove` action tombstones a file. Removed files are not physically deleted until `VACUUM` runs after the retention window.
- File compaction (`OPTIMIZE`) issues `Add` for new files and `Remove` for old; `dataChange=false` signals no logical change, so downstream streams skip these commits.

### Concurrency model

- **Optimistic concurrency**. The first writer to commit at version `v+1` wins; the loser retries on `v+2` after re-reading.
- **Conflict detection**: based on which files each writer touched. Two writers updating disjoint partitions can both commit; both updating the same file conflict.
- **MERGE** is heavy because it can touch many files; high-MERGE workloads benefit from clustering, deletion vectors, and partitioning to keep conflict surface low.

### Deletion vectors

- Stored as separate files referenced by `Add.deletionVector`.
- `DELETE` / `UPDATE` / `MERGE` write a deletion-vector file marking which row positions in a Parquet file are deleted, instead of rewriting the whole file.
- Read path applies the DV at scan time (Photon handles natively; some non-Databricks readers do not yet).
- Periodically OPTIMIZE compacts deletion vectors back into rewritten Parquet files.

### Liquid clustering

- `CLUSTER BY (col1, col2)` is declared at create time.
- New writes are routed to clusters via a Z-order-like hash of the cluster columns; small clusters merge over time.
- Adding/removing cluster columns is a no-rewrite metadata change (unlike `PARTITIONED BY`).
- Best when query predicates target the cluster columns and selectivity matters.

### Column mapping

- Two modes: **id** (Parquet field IDs reference logical names) and **name** (logical name → physical name map).
- Required for `ALTER TABLE RENAME COLUMN` and `DROP COLUMN`.
- Once enabled, only readers supporting column mapping can read the table (raise the `minReaderVersion`).

### Predictive Optimization

- UC managed tables only (not external tables, not OpenSharing recipient tables).
- Background service that scans table state (small files, write rate, query patterns) and runs OPTIMIZE / VACUUM when worthwhile.
- Enabled by default for accounts created on or after November 2024; gradual rollout to existing accounts completed in 2025.
- Bills against your account but typically cheaper than naive nightly maintenance jobs because it doesn't run when it wouldn't help.

### Row tracking

- Enabled per-table via `ALTER TABLE … SET TBLPROPERTIES ('delta.enableRowTracking' = 'true')`.
- Adds two hidden metadata columns: `_metadata.row_id` (a stable unique identifier that survives MERGE and UPDATE) and `_metadata.row_commit_version` (the table version at which the row was last modified).
- Enables row-level lineage — trace which rows moved or changed between commits.
- All Apache Iceberg v3 tables include row tracking natively; Delta's row tracking is the equivalent capability.
- Enabling on an existing table requires pausing concurrent writes; may create multiple new table versions.
- Bumps the Delta writer protocol version. GA since DBR 14.0.

### Change Data Feed (CDF)

- When enabled, every commit also writes per-row change records under `_change_data/`.
- Consumers read via `SELECT * FROM table_changes('catalog.schema.table', startingVersion, endingVersion)`.
- CDF is the typical primitive for incrementally feeding silver/gold tables and for `APPLY CHANGES INTO` downstream.

### Universal Format (UniForm)

- Same Parquet files; additional Iceberg metadata (IcebergCompatV2) generated atomically with each Delta commit. Hudi support has been removed from UniForm; the feature is now Iceberg-only.
- Non-Databricks readers (e.g., Snowflake on Iceberg, Trino on Iceberg, query engines using the Iceberg REST Catalog API) can read the table without copy.
- **OpenSharing via Iceberg REST Catalog**: D2O recipients can now read Delta tables with UniForm enabled as Iceberg tables using the Iceberg REST Catalog API (Public Preview, requires Runtime 16.1+).
- **VACUUM** now deletes untracked files in the `metadata/` directory while preserving Iceberg metadata that clients need (Runtime 17.2+).
- Trade-off: small metadata write cost on every commit; some Delta features (deletion vectors with certain config) limit what Iceberg readers can see.

### Limits / realities

- Default partition column behavior is a footgun on high-cardinality columns. Prefer liquid clustering.
- Time travel + `VACUUM` are in tension — `VACUUM` past retention deletes data needed by old reads. Default 7-day retention is a reasonable floor.
- Cross-region storage adds commit latency: every write does at least one PUT to the log.
- Stats are only collected on the first 32 columns by default. For wide tables, raise `delta.dataSkippingNumIndexedCols` or reorder columns so query-hot cols come first.
- Streaming reads with `ignoreChanges = true` skip data changes from compaction but can still see deletes — design downstream accordingly.

### Mental model

Delta is **"Parquet with a transaction log on the side"** — and that log is what gives you ACID, time travel, CDF, and concurrent writers. Everything else (clustering, deletion vectors, UniForm, predictive optimization) is built on top of that log. Mastery is mostly understanding the log + the optimistic concurrency contract + which features bump the protocol.

---

## Anti-patterns — where you should not use Delta Lake

- **Sub-millisecond key-value lookups.** Delta is an analytical format. Use Lakebase (Postgres) or a real KV store.
- **Tiny, frequent point updates to a single row.** MERGE has fixed overhead per commit. Batch them.
- **High-cardinality partition columns.** Partitioning by user_id or timestamp-to-the-second is a classic disaster — millions of tiny files. Use liquid clustering instead.
- **As a substitute for an OLTP database.** No row-level locking, no per-row latency guarantees, no FK enforcement.
- **Single-tenant tables shared by N producers writing concurrently to overlapping files.** Optimistic concurrency creates write amplification under contention. Partition / cluster to reduce overlap, or serialise writes.
- **External tables with no maintenance owner.** External Delta tables outside Predictive Optimization scope require explicit OPTIMIZE/VACUUM. Without an owner, they bloat.
- **Pure append + never-read workloads.** Plain Parquet + Hive (or nothing) is fine and cheaper if you'll never query it.
- **Using `PARTITIONED BY` on new tables.** Liquid clustering exists; partitioning is mostly legacy.
- **Treating `time travel` as a backup.** It's bounded by retention. Use Delta clone or external backup for real recovery.
- **Storing huge blobs in columns.** Delta isn't a blob store. Put files in UC Volumes and reference by path.

## Weaknesses — areas Delta Lake does not do well

- **Many small commits create many small log entries.** Streaming sources should commit on a sensible micro-batch cadence; one-row-per-commit destroys read performance.
- **Stats only on first N columns by default.** Wide tables need explicit configuration; otherwise data skipping is partial.
- **MERGE performance under contention is rough.** Disjoint MERGE partitions are fine; overlapping ones cause optimistic-concurrency retries.
- **Schema evolution doesn't auto-handle renames without column mapping.** Enabling column mapping bumps protocol version and restricts reader compatibility.
- **Deletion vectors aren't universally supported by non-Databricks readers.** Pre-V3 readers may see deleted rows. Mitigate via UniForm or periodic OPTIMIZE-to-rewrite.
- **VACUUM safety is on the operator.** Setting low retention then VACUUM-ing destroys time-travel and active queries. Default 7 days exists for a reason.
- **Cross-region writes have higher commit latency.** Every commit touches the log; geo-replicated buckets pay for it.
- **Reader/writer protocol coupling.** Enabling new features (DV, type widening, etc.) raises the protocol version; old engines can break.
- **Audit/lineage is in UC, not Delta itself.** Outside UC, Delta has commit info but no first-class audit trail by user.
- **Z-order is legacy.** Many existing tables still use it; migrating to liquid clustering requires care.
- **Type widening scope is bounded.** GA since DBR 15.4 LTS, but only a defined set of widening operations (BYTE→INT→BIGINT→DECIMAL/DOUBLE, DATE→TIMESTAMP_NTZ, etc.) are supported. Type changes outside this set still require a rewrite. Enabling widening bumps the protocol; tables require DBR 15.4 LTS minimum to read.
- **Large checkpoint files for huge tables.** Multi-part v2 checkpoints exist but require careful enablement.

---

## Sources

- [Delta Lake docs (Databricks)](https://docs.databricks.com/aws/en/delta/)
- [Delta protocol spec (open source)](https://github.com/delta-io/delta/blob/master/PROTOCOL.md)
- [Liquid clustering](https://docs.databricks.com/aws/en/delta/clustering)
- [Deletion vectors](https://docs.databricks.com/aws/en/delta/deletion-vectors)
- [Predictive Optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)
- [UniForm](https://docs.databricks.com/aws/en/delta/uniform)
- [Row tracking](https://docs.databricks.com/aws/en/delta/row-tracking.html)
- [Type widening](https://docs.databricks.com/aws/en/delta/type-widening.html)
- [Glossary](../Glossary/glossary.md)
