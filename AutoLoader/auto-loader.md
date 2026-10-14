# Auto Loader

## Level 100 — What it is

Auto Loader is a **Structured Streaming source** (`cloudFiles`) that incrementally ingests new files from cloud object storage into Delta tables. It tracks which files it has seen, infers and evolves schema, and scales from kilobyte-per-day to millions-of-files-per-day workloads.

- **Incremental**: only reads new files since the last micro-batch.
- **Schema-aware**: infers schemas, evolves on new columns, quarantines bad rows.
- **Scales**: works for tens to billions of files via a file-notification mode that avoids re-listing.

## Level 200 — The product surface

- **Source format**: `cloudFiles` Spark source with options like `cloudFiles.format` (`json|csv|parquet|avro|binaryFile|text|orc|xml`).
- **Discovery modes**:
  - **Directory listing** — periodically lists the source path. Fine up to ~10⁵ files. (`cloudFiles.useIncrementalListing` is **deprecated** — migrate to file notification with file events.)
  - **File notification** (recommended for most workloads) — two sub-variants:
    - *Classic*: provisions a cloud event subscription (SQS/SNS on AWS, Event Grid + Queue on Azure, Pub/Sub on GCP); requires elevated IAM for auto-creation.
    - *File events* (`cloudFiles.useManagedFileEvents=true`, DBR 14.3 LTS+, **currently recommended**): shared notification queue per UC external location; Databricks manages setup; no per-stream IAM provisioning required.
- **Schema management**:
  - `cloudFiles.schemaLocation` — directory where the inferred schema and recent samples live.
  - `cloudFiles.schemaEvolutionMode` — `addNewColumns` (default), `addNewColumnsWithTypeWidening` (new; also widens compatible types, e.g., int→long), `rescue`, `failOnNewColumns`, `none`.
  - `cloudFiles.inferColumnTypes` — true for JSON to bump strings to typed columns.
  - **Rescued data column** (`_rescued_data`) — JSON-encoded bucket for unparseable / unexpected fields. Keeps you from losing data when schema mismatches.
- **State**: a RocksDB-backed checkpoint tracks seen file paths.
- **Triggers**: `Trigger.AvailableNow()` for "process everything that's there and stop" (great for triggered jobs), or continuous micro-batch.
- **Sinks**: any Delta destination; commonly into an LDP streaming table or a `foreachBatch` for custom logic.
- **Auth**: storage credentials via UC external location (recommended) or instance profile / managed identity.

## Level 300 — Architecture

```
Cloud storage path
        │
        ├─[Directory listing]──► periodic LIST                   ──┐
        │                                                          ├──► [Auto Loader micro-batch] ──► Delta sink
        └─[File notification]──► SQS/SNS, Event Grid, Pub/Sub ─────┘                ▲
                                                                                    │
                                                          RocksDB checkpoint (seen files)
                                                          schemaLocation (inferred schema)
```

**Two discovery modes:**

- **Directory listing** does paginated `LIST` calls each trigger. Cost is API-quota and listing latency, both proportional to total file count. `cloudFiles.useIncrementalListing` is **deprecated**; Databricks recommends migrating to file notification mode with file events.
- **File notification** creates a cloud-native subscription; listing is no longer in the hot path. Latency drops to seconds, cost detaches from total file count. Two sub-variants:
  - *Classic*: per-stream SQS/SNS, Event Grid, or Pub/Sub subscription; requires IAM management (auto-create or pre-provision).
  - *File events* (`cloudFiles.useManagedFileEvents=true`, DBR 14.3 LTS+): shared queue per UC external location; Databricks manages the subscription entirely; no per-stream IAM needed. **This is the current recommended approach.**

**Schema handling**: schema files live next to the checkpoint. On startup, Auto Loader reads the latest schema, samples files, and decides whether a new column or type change has appeared. The `_rescued_data` column captures any field that doesn't match the schema — including fields from newer versions of source data.

**State**: the checkpoint includes both (a) Structured Streaming offsets and (b) the file ingestion state (`cloudFiles.allowOverwrites=false` by default, so paths are not re-read). RocksDB keeps this scalable even at billions of files.

## Level 400 — Deep internals

### Directory-listing mode internals

- Auto Loader paginates LIST calls. Default page size is cloud-specific.
- **`cloudFiles.useIncrementalListing` is deprecated.** It required lex-time-sortable file names to focus on the highest-seen prefix. Databricks recommends migrating to file notification mode with file events instead.
- For S3, S3 prefix listing optimizations still apply when files are partitioned by date.

### File-notification mode internals

**Classic notification mode:**
- AWS: SNS topic + SQS queue subscribed to the bucket. Auto Loader is the SQS consumer. Long-poll, visibility timeout, dead-letter queue all configurable.
- Azure: Event Grid + Storage Queue; Auto Loader consumes the queue.
- GCP: Pub/Sub topic + subscription.
- Permissions: by default Auto Loader auto-creates the queue/topic. You can pre-provision and pass IDs via `cloudFiles.queueUrl`, `cloudFiles.subscriptionId`, etc. — useful when ops controls IAM.

**File events mode (recommended, DBR 14.3 LTS+):**
- Set `cloudFiles.useManagedFileEvents=true` and ensure the source is on a UC external location with file events enabled.
- Uses a single shared notification queue per external location rather than a per-stream subscription. Databricks manages the entire subscription lifecycle — no per-stream IAM setup required.
- Eliminates the "elevated permissions for auto-create" friction of classic mode.
- Note: file events mode does not guarantee the order in which files are discovered or processed; design pipelines to tolerate out-of-order file arrivals.

**Backfill (both modes):** when first enabling notification mode on an existing bucket, set `cloudFiles.includeExistingFiles=true` for a one-time directory-listing backfill.

### RocksDB state internals

- The state stores SHA-256 of each seen file path. Mature, fast, scales.
- TTL: `cloudFiles.maxFileAge` defaults to 7 days for notification mode; files older than this are forgotten and may be re-ingested if they re-appear in events. Set higher for slow-arriving event streams.
- The state lives under `checkpointLocation/sources/<source-id>/`.

### Schema inference + evolution

- On first run with no schema file: sample N files, infer schema (JSON gets type inference if enabled).
- On subsequent runs: schema file is the source of truth. When new columns are detected, the stream stops with an `UnknownFieldException` after Auto Loader updates the schema location. Configure `cloudFiles.schemaEvolutionMode`:
  - `addNewColumns` (default) — stream fails after schema update; Auto Loader restarts and picks up the new column. Databricks recommends pairing with Lakeflow Jobs for automatic restarts.
  - `addNewColumnsWithTypeWidening` — like `addNewColumns` but also widens compatible types automatically (e.g., int→long).
  - `rescue` — new fields land in `_rescued_data` JSON column; schema itself doesn't change.
  - `failOnNewColumns` — fails permanently; requires manual intervention to resume.
  - `none` — ignores new columns without schema updates.
- Type changes are not auto-handled. New column with conflicting type either widens (compatible) or routes the row to `_rescued_data`.

### Performance tuning

- `cloudFiles.maxFilesPerTrigger` and `cloudFiles.maxBytesPerTrigger` cap micro-batch size — important for shaping spike-of-files behavior.
- `cloudFiles.partitionColumns` — declare partition columns if data is Hive-style partitioned and you want them preserved.
- For binary file ingestion at scale, `binaryFile` format + Spark UDFs is the standard pattern; consider parsing async into a downstream stream.

### Exactly-once semantics

- Auto Loader → Delta sink is **exactly-once** because: (a) seen-files state is transactional with the Delta commit (idempotent path-based dedup), (b) Delta itself is ACID, (c) Structured Streaming offsets are committed alongside.
- This breaks if you `cloudFiles.allowOverwrites=true` (modified files re-ingested) or if you reset the schema location alone without the checkpoint.

### Limits / realities

- One Auto Loader source = one schema location = one logical schema. Two streams cannot share a schema location.
- File-notification mode requires Auto Loader-managed subscriptions in default config; some security teams disallow auto-creation. Pre-provision in those cases.
- Listing-mode latency grows with file count; switch to notification mode well before pain.
- XML and unstructured text need additional parsing logic downstream.
- `_rescued_data` is a parsed JSON column — if you forget to project it, you lose rescued fields silently.

### Mental model

Auto Loader is **`fileStream` for adults**: Structured Streaming `cloudFiles` + a scalable seen-files state + schema management + cloud-native event integration. The two design choices that matter most are **listing vs. notification** (drives cost/latency curve) and **schemaEvolutionMode** (drives blast radius of upstream changes).

---

## Anti-patterns — where you should not use Auto Loader

- **Reading from a single specific file path.** Use `spark.read` once. Auto Loader is for *streams* of files.
- **Small static datasets that won't grow.** Overkill — just `COPY INTO` or a one-shot read.
- **Sources where files mutate in place.** Auto Loader skips re-read by default. If your producer overwrites files, set `allowOverwrites=true` knowingly (and accept duplicates) or change the source pattern.
- **Multiple writers to the same prefix without coordination.** New files from many writers are fine; but if two Auto Loader streams target the *same checkpoint*, you get state corruption.
- **Sub-second freshness on listing mode.** Listing mode is minutes-class. Use notification mode or push to Kafka.
- **As a CDC replacement for databases.** Auto Loader reads files. Use Lakeflow Connect DB connectors for database CDC.
- **Heavily nested / variable JSON without `_rescued_data` discipline.** You'll lose fields silently or have unstable schemas.
- **Cross-cloud sources.** Auto Loader is per-cloud (SQS/EventGrid/PubSub). For multi-cloud, run one Auto Loader per source cloud.
- **Streaming-table-equivalent ingestion in LDP without using Auto Loader directly.** Inside LDP, `read_files()` or `cloudFiles` source via SQL is the idiomatic path — don't roll your own.
- **Tracking-arbitrary-bucket-state without a Delta sink.** Auto Loader's exactly-once guarantee hinges on Delta. Writing to non-Delta sinks loses that contract.

## Weaknesses — areas Auto Loader does not do well

- **Listing-mode cost on huge buckets.** LIST API calls add up; cost grows with file count, not change rate. Switch to notification mode.
- **Classic notification setup has IAM friction.** Auto-creation requires elevated permissions; pre-provisioning means coordinating SQS/EventGrid/PubSub with ops. The new file events mode (`cloudFiles.useManagedFileEvents=true`) eliminates this problem for UC external location sources.
- **`maxFileAge` foot-gun.** Default 7 days; if your events arrive late in notification mode (e.g., reprocessed by source), you may re-ingest.
- **Schema inference is sampling-based.** A rare type variation in unsampled files can land in `_rescued_data` quietly.
- **Two writers, one schema = broken.** No multi-tenant sharing of a schema location.
- **No first-class deduplication beyond file-path.** Two files with same content but different names will both be ingested.
- **XML, fixed-width, and unstructured text need post-processing.** Format support beyond JSON/CSV/Parquet is thinner.
- **Listing-mode partitions need hints.** `partitionColumns` is needed when you want Hive-style partitions preserved; otherwise they become regular columns.
- **`_rescued_data` is opt-in to query.** Forget it and you lose visibility into schema drift.
- **State migration is hard.** Moving from listing to notification mode or vice versa requires careful state handling; usually easier to start fresh on a new checkpoint.
- **Inferred schema for CSV is often disappointing.** Provide an explicit schema for CSV; type inference is generic.
- **Backfill of an existing bucket on notification mode is one-shot.** `includeExistingFiles=true` is a one-time switch — get it right on enable.

---

## Sources

- [Auto Loader docs](https://docs.databricks.com/aws/en/ingestion/auto-loader/)
- [Auto Loader options reference](https://docs.databricks.com/aws/en/ingestion/auto-loader/options)
- [File notification mode](https://docs.databricks.com/aws/en/ingestion/auto-loader/file-notification-mode)
- [Schema inference and evolution](https://docs.databricks.com/aws/en/ingestion/auto-loader/schema)
- [Glossary](../Glossary/glossary.md)
