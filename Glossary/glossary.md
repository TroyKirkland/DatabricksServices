# Databricks Glossary

Shared term reference for the per-product docs in `../`. Add new entries here whenever a product doc introduces a load-bearing term.

> Convention: product docs cite terms as `[[glossary:term]]`. Terms are alphabetised within each category. Italicised cross-references link to other glossary entries.

---

## Compute & runtime

- **Classic compute** — Spark clusters that you size and that run inside your cloud account's VPC/VNet. Slower to start, longer to keep warm, can use init scripts and custom libraries. Required for *ingestion gateways* and some legacy workloads.
- **Cluster** — A driver + worker VM group running Spark. Multi-node, single-node, or pool-backed. Has a *Databricks Runtime* version.
- **Databricks Connect** — Client library that lets a local IDE/Python process talk to a remote Databricks cluster via *Spark Connect*. Replaces the legacy "DB Connect v1" RPC.
- **Databricks Runtime (DBR)** — Versioned bundle of Spark + Photon + ML libraries + cloud SDKs. Long-Term Support (LTS) releases supported ~3 years (36 months). ML and GPU variants pre-install additional libraries/drivers.
- **DBU (Databricks Unit)** — Unit of consumption Databricks bills in. Different SKUs (Jobs, All-Purpose, SQL, DLT/LDP, Serverless) have different DBU rates.
- **Photon** — Databricks' C++ vectorized execution engine. Engages automatically for supported SQL/DataFrame operators; falls back to JVM Spark when unsupported.
- **Pool** — Pre-warmed VMs that clusters can draw from to skip cold-start latency.
- **Serverless compute** — Databricks-managed compute that runs in Databricks' account, not yours. Scale-to-zero, fast startup, no VPC. Available for SQL Warehouses, Jobs, Notebooks, LDP, and Model Serving.
- **Spark Connect** — Open-source client/server protocol that decouples Spark client code from the JVM driver. Underpins *Databricks Connect*.
- **SQL Warehouse** — A SQL-only compute endpoint. Three flavors: **Serverless** (fastest, managed by Databricks), **Pro** (in your VPC, supports advanced governance), **Classic** (legacy in-your-VPC).

## Storage, format, governance

- **ACID** — Atomic-Consistent-Isolated-Durable transactions. Delta provides ACID at the table level via the *transaction log*.
- **CDF (Change Data Feed)** — Delta feature that exposes per-row change events (`_change_type` insert/update/delete) for downstream consumers.
- **Column mapping** — Delta feature decoupling logical column names from physical Parquet field IDs. Required for renames and `_change_type` on some operations.
- **Delta Lake** — Open transactional storage format on top of Parquet, with a JSON+checkpoint transaction log in `_delta_log/`.
- **Delta Sharing** — Open protocol for sharing Delta tables across orgs without copy. D2D = Databricks-to-Databricks (richer); D2O = Databricks-to-Open-source (basic), now branded *OpenSharing*.
- **Delta UniForm** — A Delta table that simultaneously exposes Iceberg metadata (IcebergCompatV2) so non-Databricks readers can consume it as Iceberg. (Hudi output was removed; UniForm is Iceberg-only.)
- **Deletion vector** — Soft-delete metadata that marks Parquet rows as deleted without rewriting the file. Speeds MERGE/DELETE/UPDATE.
- **External location** — UC object pointing at a cloud-storage prefix with a *storage credential*. Where external tables / volumes live.
- **External table** — UC table whose data lives outside the metastore-managed root.
- **Hive Metastore (HMS)** — Legacy per-workspace metastore. Pre-UC. Avoid for new work.
- **Liquid clustering** — Delta layout strategy that auto-clusters data on declared columns; supersedes `PARTITIONED BY` and `ZORDER` for most cases. Supports hierarchical clustering and partial reclustering (`OPTIMIZE … FULL`).
- **Managed Iceberg table** — UC managed table stored in Apache Iceberg format (GA 2026), governed like a managed Delta table. Distinct from *Delta UniForm*, which exposes Iceberg metadata over Delta files.
- **Managed table** — UC table whose data is stored in the metastore's managed root. UC owns the files' lifecycle.
- **Metastore** — UC's top-level catalog container, one per region per account. Holds catalogs → schemas → tables/views/volumes/models/functions.
- **OpenSharing** — Databricks' name for sharing with non-Databricks recipients over the open *Delta Sharing* protocol (including an Iceberg REST Catalog API). The open-recipient path formerly described as D2O.
- **OPTIMIZE** — Delta command that compacts small files. With `ZORDER BY` or liquid clustering, also reorganises layout.
- **Predictive optimization** — UC feature that automatically runs OPTIMIZE / VACUUM / ANALYZE on managed tables when worthwhile. Enabled by default for accounts created on/after Nov 2024.
- **RBAC (Role-Based Access Control)** — UC capability (GA 2026) letting a user assume a named role scoped to only that role's privileges. Complements *ABAC*.
- **Row filter / column mask** — UC functions applied at query time to filter rows or mask cells based on the caller's identity/attributes.
- **Row tracking** — Delta feature assigning a stable `_metadata.row_id` and `row_commit_version` per row for row-level lineage. GA since DBR 14.0.
- **Schema** (UC sense) — Mid-tier namespace inside a catalog. `<catalog>.<schema>.<table>`. (Spark also uses "schema" for table structure — context disambiguates.)
- **Storage credential** — UC object wrapping an IAM role / managed identity used to access cloud storage.
- **System tables** — UC catalog `system.*` exposing billing usage, audit, lineage, query history, etc.
- **Time travel** — Querying a Delta table at a prior version (`VERSION AS OF`) or timestamp (`TIMESTAMP AS OF`). Bounded by VACUUM retention.
- **Type widening** — Delta schema-evolution feature that widens a column's type (e.g. int→long) without rewriting files. GA since DBR 15.4 LTS; bounded to a defined set of widening paths.
- **Unity Catalog (UC)** — Databricks' unified governance plane: tables, models, volumes, ML features, lineage, audit, ABAC, fine-grained access control.
- **UC Volume** — A governed non-tabular storage abstraction. Path scheme `/Volumes/<catalog>/<schema>/<volume>/...`. Managed or external.
- **VACUUM** — Delta command that physically removes files no longer in the latest version after a retention window (default 7 days).
- **Z-order** — Multi-dimensional Delta data-skipping ordering. Largely superseded by liquid clustering.

## Streaming & change capture

- **APPLY CHANGES INTO** — LDP statement that applies a CDC change feed to a streaming table, handling inserts/updates/deletes with optional SCD Type 1 or 2. Superseded by *AUTO CDC* (identical syntax; the older API still functions but is no longer recommended).
- **AUTO CDC** — Current LDP API that replaced *APPLY CHANGES INTO* for applying a CDC feed to a streaming table. `AUTO CDC FROM SNAPSHOT` (Python) compares sequential full snapshots with no change feed. Supports bitemporal SCD Type 2 tracking.
- **Auto Loader** — `cloudFiles` Structured Streaming source for incremental file ingestion. Two discovery modes: directory listing and file-notification (event-driven, incl. *file events mode*).
- **CDC (Change Data Capture)** — A capture mechanism (DB-specific) that emits insert/update/delete events with column-level history. Heavier than Change Tracking.
- **Change Tracking** (SQL Server) — Lightweight SQL Server feature that records *which rows* changed (no values, no operation type). Lower source overhead than CDC.
- **File events mode** — Auto Loader file-notification sub-variant (`cloudFiles.useManagedFileEvents=true`, DBR 14.3 LTS+) using a Databricks-managed shared queue per UC *external location*; no per-stream cloud IAM. Databricks-recommended over the deprecated incremental listing.
- **Idempotent write** — A write that produces the same final state regardless of how many times it's applied. `AUTO CDC` is idempotent on its sequence key.
- **LSN (Log Sequence Number)** — Monotonic position in a source database's transaction log (or equivalent: GTID for MySQL, SCN for Oracle, WAL LSN for Postgres). Used by CDC consumers as their checkpoint.
- **Materialized view (MV)** — A streaming/batch-refreshed table whose contents are a query result. In LDP and DBSQL.
- **SCD Type 1 / Type 2** — Slowly-changing-dimension patterns. Type 1 keeps current state; Type 2 keeps a row per historical state with `__START_AT`/`__END_AT`.
- **Streaming table (ST)** — A Delta table written incrementally by a streaming source, declared in LDP or DBSQL.
- **Structured Streaming** — Spark's micro-batch/continuous streaming framework. Underpins Auto Loader, LDP streaming tables, and most Databricks streaming.
- **Zerobus** — Databricks' gRPC ingest SDK that writes directly to Delta tables without a Kafka/Kinesis hop.

## Orchestration & deployment

- **Declarative Automation Bundles (DAB)** — YAML-defined bundle of jobs/pipelines/dashboards/apps that can be deployed via `databricks bundle deploy`. CI/CD primitive. Formerly *Databricks Asset Bundles*.
- **Job** (Lakeflow Job) — Orchestration unit composed of tasks with dependencies, triggers, retries, and notifications.
- **Notebook** — Mixed-language interactive document (Python/SQL/Scala/R) stored in workspace or Git.
- **Repos / Git folder** — Workspace folder synced to a Git remote.
- **Service principal** — Non-human identity for automation; supports OAuth, PAT, or federated auth.
- **Task** — One step inside a Job; can be a notebook, Python wheel, JAR, SQL query, dbt, LDP pipeline trigger, or for-each.
- **TaskValues** — Small key-value payload one task can publish for downstream tasks to read.
- **Trigger** — How a Job starts: scheduled (cron), continuous, file arrival, or table update.

## SQL & analytics

- **AI/BI Dashboards** — Successor to Lakeview; native dashboarding inside Databricks. Datasets + widgets + parameters.
- **AI/BI Genie** — NL-to-SQL conversational interface scoped to a *Genie Agent*.
- **Catalog Federation** — *Lakehouse Federation* mode giving direct object-storage access to foreign catalogs (Glue, external/legacy Hive metastore, Salesforce Data 360, Snowflake, Palantir Foundry, Workday) without a JDBC hop; auto-refreshes at query time.
- **DBSQL** — Databricks SQL: warehouses, queries, dashboards, alerts.
- **Genie Agent** — A bounded set of tables + curated instructions + example SQL (and optional *metric views*) that an LLM uses to answer questions. Formerly *Genie Space*; the end-user NL interface is *Genie One*.
- **Lakehouse Federation** — UC foreign catalogs that proxy queries to Snowflake, Postgres, SQL Server, BigQuery, Redshift, MySQL, Oracle, Teradata, etc. Two modes: *Query Federation* (JDBC pushdown) and *Catalog Federation* (direct storage).
- **Lakeview** — Legacy name for AI/BI Dashboards.
- **Materialized view** *(see also Streaming section)* — In DBSQL, an MV refreshed by a serverless pipeline behind the scenes.
- **Metric View** — UC semantic-layer object defining reusable business measures and dimensions, queryable via the `MEASURE` aggregate and usable as a dashboard/Genie dataset.
- **Pipe syntax (`|>`)** — DBSQL operator-chained SQL alternative to nested subqueries.
- **Query Federation** — The original *Lakehouse Federation* mode: JDBC pushdown to live external databases (Snowflake, Postgres, SQL Server, BigQuery, Redshift, MySQL, Oracle, Teradata); requires manual schema refresh.
- **Statement Execution API** — REST API to submit SQL to a warehouse without JDBC/ODBC.

## AI / ML

- **Agent Bricks** — Eval-driven builder for production agents: Knowledge Assistant, Genie Agent, Multi-Agent Supervisor.
- **ChatAgent / ResponsesAgent** — MLflow agent interfaces for serving conversational/agentic models with tracing.
- **External model** — Model Serving endpoint that proxies to an outside provider (OpenAI, Anthropic, Cohere, Bedrock). Subject to *Unity Gateway* policies.
- **Feature Store / Feature Engineering in UC** — Feature tables in UC + optional online tables; supports point-in-time joins for training-serving parity.
- **Feature Views** — Public-Preview declarative feature definitions in Feature Engineering (DeltaTableSource, StreamSource, RequestSource). Requires DBR 17.0 ML+ or serverless.
- **Foundation Model APIs (FMAPI)** — Databricks-hosted foundation models. Pay-per-token for some, Provisioned Throughput for production.
- **Full-Text Search Index** — Mosaic AI Vector Search index type (Beta) doing BM25 keyword search on text columns with no embeddings; storage-optimized endpoints, triggered sync only.
- **Logged Models** — MLflow 3 first-class entity tracking a model's development arc (runs, metrics, params, code); distinct from a UC registered model version.
- **MCP server (Model Context Protocol)** — Standardised tool/API interface agents call to reach external systems; a resource type for Apps, DABs, and Agent Bricks Multi-Agent Supervisor, governed by *Unity Gateway*.
- **MLflow** — Open-source experiment tracking + model registry + tracing + evaluation. On Databricks, registered models live in UC.
- **Model Serving endpoint** — Hosted inference endpoint for an MLflow model or external model. Serverless, scale-to-zero, GPU-optional.
- **Mosaic AI** — Umbrella brand covering Model Serving, Vector Search, *Unity Gateway*, Agent Bricks, Foundation Model APIs.
- **Online table** — Low-latency replica of a Delta feature table used at inference time (backed by Lakebase Autoscaling).
- **Pay-per-token** — Billing model for FMAPI where you pay per input+output token; shared infra.
- **Payload table** — Auto-generated Delta table where Model Serving writes request/response logs for monitoring and offline eval.
- **Provisioned Throughput** — FMAPI billing model where you reserve dedicated capacity for predictable latency and price.
- **Reciprocal Rank Fusion (RRF)** — Algorithm Mosaic AI Vector Search hybrid queries use to merge vector + keyword result sets; hybrid results capped at 200.
- **Unity Gateway** — Governance/policy layer in front of model endpoints, agents, and MCP servers: rate limits, PII detection, payload logging, fallback and smart routing. Formerly *Mosaic AI Gateway*.
- **Vector index** — Mosaic AI Vector Search index over a column of embeddings, optionally Delta-synced.

## Apps & operational data

- **app.yaml** — Manifest for a Databricks App declaring command, resources, scopes.
- **Databricks App** — Hosted Streamlit/Dash/Flask/FastAPI/Node web app inside Databricks with workspace OAuth.
- **Lakebase** — Managed Postgres on Databricks; provisioned and autoscale flavors; supports branching and scale-to-zero.
- **Marketplace listing** — A data product, AI model, or notebook published in Databricks Marketplace and delivered via Delta Sharing.

## Networking & identity

- **OAuth M2M / U2M** — Machine-to-machine (service principal client credentials) vs user-to-machine (interactive) OAuth flows for Databricks auth.
- **Private Link** — Cloud-provider private endpoint to reach Databricks control plane and storage privately.
- **SCIM** — Identity sync protocol for users/groups from your IdP into Databricks.

## Legacy / deprecated terms

- **Delta Live Tables (DLT)** — Renamed to *Lakeflow Spark Declarative Pipelines (LDP)*.
- **Workflows** — Renamed to *Lakeflow Jobs*.
- **Lakeview** — Renamed to *AI/BI Dashboards*.
- **Feature Store (workspace)** — Migrated to *Feature Engineering in Unity Catalog*.
- **Databricks Asset Bundles** — Renamed to *Declarative Automation Bundles (DAB)*.
- **Mosaic AI Gateway** — Renamed to *Unity Gateway*.
- **Genie Space** — Renamed to *Genie Agent* (end-user interface: Genie One).
