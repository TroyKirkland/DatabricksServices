# Feature Engineering in Unity Catalog

> Successor to the legacy workspace-scoped **Feature Store**. Same idea, now in UC with first-class governance.

## Level 100 — What it is

Feature Engineering in UC is the **feature store** for ML on Databricks. You compute features as Delta tables (offline) and optionally publish them to low-latency **online tables** (for inference). The framework records which features each model uses, enabling **training-serving parity** and **point-in-time correctness** for batch training.

- **Feature tables** are just Delta tables in UC with a primary key + (optional) timestamp.
- **Online tables** are low-latency replicas synced from offline.
- **Training set logging** ties model versions to the features they were trained on.

## Level 200 — The product surface

- **Feature table** — Delta table with declared **primary key**(s); optionally a **timestamp key** for point-in-time features.
- **Online table** — synced Delta-table view served from low-latency backing (Lakebase / online store), accessible via REST.
- **Feature Engineering Python client** — `databricks-feature-engineering` package; create tables, log training sets, look up features at inference.
- **Training set** — `fe.create_training_set(df, feature_lookups=[...], label="y")` joins point-in-time-correct features for batch training.
- **Online lookup** — at inference, the served model (often via Model Serving) calls online tables for the feature row; UC-governed.
- **Feature Views** (Public Preview, DBR 17.0 ML+) — declarative feature definitions. Declare feature logic (windowed aggregations, column transforms) once; the framework materialises to offline or online stores. Three source types: `DeltaTableSource`, `StreamSource` (Kafka, ~200ms p99), `RequestSource` (request-time scalars). Enable via workspace Previews page.
- **Feature serving endpoints** — dedicated endpoints that expose feature lookups via REST.
- **Lineage** — features used → models trained → models deployed: tracked through UC.

## Level 300 — Architecture

```
[Source data] ──► [Feature engineering pipeline] ──► [Feature table (Delta, UC)]
                                                            │
                                                            ├─► [Training-time read]: point-in-time join
                                                            │       └ produces a training set logged on the MLflow run
                                                            │
                                                            └─► [Online sync]: Delta → online table
                                                                    │
                                                                    └─► Inference-time lookup by PK (low-latency)
```

- **Offline pipeline** is yours (Spark / SQL / LDP) — write to a UC Delta table with declared PK / timestamp.
- **Online sync** is managed: Databricks keeps the online table consistent with the source via CDF.
- **Model wiring**: when you log a model with `fe.log_model(...)`, Databricks records the feature dependencies; at serving time, the model can look up features automatically.

## Level 400 — Deep internals

### Feature table conventions

- Declare PKs via `CREATE TABLE ... TBLPROPERTIES('delta.feature.allowColumnDefaults'='supported')` and the feature-engineering-specific table properties (PK columns).
- Time-series features: declare a timestamp column; point-in-time joins use this to fetch the latest-as-of-event-time row per entity.
- Schema evolution: add columns freely; remove columns rebuilds online table.

### Point-in-time join semantics

- Given an events DataFrame `(entity_id, event_ts, label)` and a feature lookup spec `(table, key_col, ts_col, [feature_cols])`, the feature engineering client builds a Spark query that finds the latest feature row per entity at-or-before each event timestamp.
- Crucial for avoiding label leakage in batch training.
- Multi-table point-in-time joins: feature tables with different timestamp grids resolved independently.

### Online tables internals

- Online table is a synced replica backed by **Lakebase Autoscaling** projects (formerly described as Lakebase Postgres). Databricks manages the storage tier.
- Three sync modes: **triggered** (scheduled incremental), **continuous** (streaming, Delta CDF), **snapshot** (one-time full copy).
- Supports up to **3 read replicas** for high availability and load distribution.
- Schema must support PK indexing; updates apply by PK.
- Read latency at inference: low-ms.
- **Constraint**: Feature Serving and Model Serving endpoints that look up features from multiple online feature stores continue working if they already exist, but **you cannot create new multi-store endpoints on Lakebase Autoscaling instances**. Design single-source online tables or consolidate lookup logic upstream.

### Logging and lineage at training

- `fe.create_training_set(df, [FeatureLookup(...)], label="y")` materialises a training DataFrame and stamps the resulting MLflow run with feature dependencies.
- `fe.log_model(model, "name", training_set=ts)` registers the model with the feature spec attached.
- Lineage flows: feature table → training set → model version → serving endpoint.

### Inference-time lookup

- A model logged with feature dependencies can be deployed to Model Serving; when called with a primary-key-bearing payload, Model Serving (or the agent code) does an online table lookup, joins, then calls predict.
- Two patterns: **automatic** (Databricks injects features) or **manual** (your code calls feature-serving endpoint).

### Feature serving endpoints

- Dedicated endpoint that exposes feature lookups via REST: `POST /serving-endpoints/<feature-endpoint>/invocations` with primary-key payload.
- Useful when the consumer isn't a Databricks-served model (e.g., a Java microservice).

### Feature Views (Public Preview)

- Feature Views let you declare feature logic as code rather than managing pre-computed Delta tables manually.
- Three source types:
  - **DeltaTableSource** — Delta Lake tables, including tables populated by Zerobus or LDP pipelines.
  - **StreamSource** — Kafka streams; achieves ~200ms p99 freshness for near-real-time feature updates.
  - **RequestSource** — Scalars passed at request time; no complex types.
- Computed via `FeatureEngineeringClient`; same client handles training, batch inference, and online serving materialization.
- Requires DBR 17.0 ML or above (or serverless).
- Currently Public Preview; enable from workspace Previews page. Expect schema and API evolution before GA.

### Limits / realities

- Online tables backed by Lakebase Autoscaling have a separate cost and storage footprint.
- Big feature tables (>1 TB) may need careful sync sizing; consider partial publishing.
- Schema changes that affect PK require full re-sync.
- Point-in-time joins are Spark-heavy; for very large training sets, pre-aggregate features.
- Online lookup latency depends on Lakebase Autoscaling tier and warm-state.
- No automatic feature pipeline orchestration — you build it with LDP/Jobs.
- Feature transformations in flight (e.g., compute embedding at lookup time) require a custom serving function; not built-in.
- You cannot create new Model Serving or Feature Serving endpoints that look up from multiple online feature stores on Lakebase Autoscaling instances.

### Mental model

Feature Engineering in UC is **"declare features once, govern them, train and serve from the same definition"**. The key insight isn't the store; it's the **point-in-time correctness** at training and the **same-features-at-serving** guarantee. Without those, you've reinvented "tables with extra steps".

---

## Anti-patterns — where you should not use Feature Engineering in UC

- **Single-use one-shot training.** Just join the data inline; don't add the feature-store layer.
- **Tables that aren't really features.** Reference data, dimensions, lookup tables — those are just tables; you don't need feature-store semantics.
- **Online lookup at OLTP scale.** Sub-ms global lookups need an in-process cache or a global KV store; online tables are low-ms, not μs.
- **Feature pipelines that change daily.** Stable features are the point; if every model needs a bespoke transform, the store is overhead.
- **No timestamp key on time-series features.** Defeats point-in-time correctness; expect label leakage.
- **Heavy embedding features in online tables.** Embeddings belong in Vector Search.
- **Many small features computed at lookup time.** Compute offline; store result.
- **Custom transforms inside the feature lookup at inference.** Pre-compute and store.
- **Online tables for huge tables you'll only query 1% of.** Costly. Project the subset offline first.
- **Using feature store as a general data API.** Use UC tables + Lakebase for that.

## Weaknesses — areas Feature Engineering in UC does not do well

- **Online table cost.** Lakebase backing is non-trivial; high-cardinality features explode.
- **Sync lag is seconds, not real-time.** For freshest data, hit Delta directly.
- **No multi-region online tables.** Online table lives in one region.
- **Schema evolution friction on PK changes.** Full re-sync.
- **Pipeline orchestration is on you.** Use Jobs + LDP.
- **Feature pipeline reproducibility is by convention.** Pin to DBR + lib versions or you'll drift.
- **Cold-start of online table on first sync is slow.** Backfilling huge tables takes time.
- **Limited transformation hooks.** Computing at lookup time isn't first-class.
- **Cross-team feature reuse needs governance.** Naming + ownership conventions critical.
- **Observability is via MLflow lineage + UC system tables.** No first-class feature-monitoring dashboard.
- **Migrations from workspace Feature Store to UC require effort.** Not a one-button move.
- **Per-feature staleness tracking is manual.** Build it via `system.access.audit` if needed.
- **Cannot create new multi-online-store endpoints on Lakebase Autoscaling.** Existing ones are grandfathered; new designs must consolidate.

---

## Sources

- [Feature Engineering in UC docs](https://docs.databricks.com/aws/en/machine-learning/feature-store/)
- [Online tables](https://docs.databricks.com/aws/en/machine-learning/feature-store/online-tables)
- [Feature Views (Public Preview)](https://docs.databricks.com/aws/en/machine-learning/feature-store/feature-views)
- [Point-in-time lookups](https://docs.databricks.com/aws/en/machine-learning/feature-store/time-series)
- [Feature serving endpoints](https://docs.databricks.com/aws/en/machine-learning/feature-store/feature-serving)
- [Glossary](../Glossary/glossary.md)
