# Lakebase

## Level 100 — What it is

Lakebase is **managed Postgres on Databricks**. It's a transactional (OLTP) database designed to live alongside the analytical lakehouse — so apps, agents, and ML models can do low-latency point reads and writes against the same UC-governed data plane. Two tiers: **Provisioned** (always-on production Postgres) and **Autoscale** (scale-to-zero, branchable, instant-restore "next-gen" Postgres).

- **Postgres-compatible**: standard wire protocol; existing drivers, ORMs, BI tools work.
- **UC-integrated**: tables visible to UC; lineage, governance.
- **Sync with Delta**: bi-directional sync patterns to/from UC Delta tables.

## Level 200 — The product surface

- **Lakebase instance** — the database server: pick provisioned (instance class + storage) or autoscale (compute units + storage).
- **Database / schema / table** — standard Postgres. Tables can be **synced tables** (one-way mirrors from Delta) or normal Postgres tables.
- **Autoscale features**:
  - **Scale-to-zero** after idle; cold-resume in seconds.
  - **Branching**: create a copy of a database (data + schema) for dev/test in seconds; cheap because it's CoW underneath.
  - **Instant restore**: restore to any point in retention window without downtime.
- **Provisioned features**:
  - Always-on; classic Postgres semantics.
  - HA replicas, automated backups, point-in-time recovery (PITR).
- **Sync from Delta** (popular pattern): Delta table → Lakebase synced table (one-way, CDF-driven).
- **Sync to Delta** (other direction): Lakebase tables exposed as UC tables via Lakehouse Federation or scheduled snapshot.
- **Access**: standard Postgres connection string (`postgres://`) + UC-managed credentials; Databricks-native clients via `databricks-sdk`.

## Level 300 — Architecture

```
[App / agent / model serving / Databricks notebook]
       │
       Postgres wire protocol
       │
       ▼
[Lakebase instance]
       ├─ Provisioned: dedicated Postgres (HA + backups)
       └─ Autoscale:   scale-to-zero, branchable, CoW storage
       │
       ▼
[Lakebase tables]
       │
       ├─ Synced tables ◄── from Delta (CDF-driven)
       └─ Native Postgres tables
       │
       ▼
[Optional exposure back to UC via Lakehouse Federation]
```

- **Two tiers** with different operational characteristics: provisioned is "Postgres-as-you-know-it"; autoscale adds modern serverless semantics (scale-to-zero, branching, CoW).
- **Synced tables** are the standard pattern for serving low-latency reads from Delta-resident data.
- **UC governance** at the instance/database/table level; cluster identity → connection identity flow.

## Level 400 — Deep internals

### Provisioned tier

- Backed by a managed Postgres cluster sized via instance class (CPU/RAM) + storage.
- HA via primary + standby; automated failover.
- PITR via base backup + WAL retention.
- Major Postgres versions supported per release notes.
- Extensions: a curated set (pgvector, others) enabled by default; custom extensions limited.

### Autoscale tier

- Built on a separation-of-storage-and-compute architecture (compute instances + storage service).
- **Branching**: create a new branch — gets a copy-on-write reference to the parent's storage at branch time. Fast, cheap. Writes on the branch diverge.
- **Scale-to-zero**: compute pauses after idle; next request resumes; pause/resume is seconds.
- **Instant restore**: pick a timestamp within retention; new branch / restored state appears immediately, no replay wait.

### Synced tables (from Delta)

- Declared with a primary key matching the Delta source.
- Sync engine consumes Delta CDF, applies inserts/updates/deletes to the Lakebase table.
- Latency: seconds typical; depends on volume and sync resources.
- Schema evolution: new columns auto-flow; type changes / renames require care.
- Online-table sister: same machinery as Feature Engineering's online tables.
- **`ai_search` on synced tables** (Beta September 2026): the `ai_search` SQL function can now search Lakebase synced tables — enables semantic vector search against Lakebase-served data without a separate Vector Search index for synced table content.

### UC integration

- Lakebase instance is a UC object; permissions managed via UC.
- Connection identity (service principal) is associated with the instance for app-side access.
- Lakebase tables can be exposed as UC tables via Federation for analytical reads (with usual federation caveats).

### Performance characteristics

- **Provisioned**: latency depends on instance class + workload; standard Postgres expectations.
- **Autoscale**: cold-resume seconds; warm steady-state matches Postgres expectations; branching is metadata-only and effectively instant.
- **Write throughput**: shaped by instance/compute size; for very high write rates, beef up tier.
- **Connection pooling**: use a pooler (pgbouncer, Databricks app connection pools) — direct connection counts are bounded.

### Use cases

- App backend (Databricks Apps reading/writing user state).
- Agent memory (sessions, preferences, conversation state).
- Online feature serving (via Feature Engineering online tables).
- Operational dashboards needing point-lookups.
- Branched envs for dev / preview / testing.
- Semantic search on synced tables via `ai_search` (Beta September 2026).

### Limits / realities

- Postgres extensions: curated subset only.
- Branching uses CoW storage; massive write divergence on branches can cost.
- Synced tables are one-way; bi-directional requires custom logic.
- Cross-region not native; one instance per region.
- Connection limits per tier; pool connections.
- Provisioned and Autoscale aren't interchangeable mid-instance; migrate via export/import.

### Mental model

Lakebase is **"the OLTP companion to the lakehouse"**. Provisioned = traditional managed Postgres but UC-integrated. Autoscale = modern serverless Postgres (scale-to-zero, branching, instant restore) that pairs naturally with ephemeral envs and AI/app workloads.

---

## Anti-patterns — where you should not use Lakebase

- **Heavy analytical queries.** Run those against Delta in DBSQL. Lakebase is OLTP.
- **Multi-TB OLAP tables.** Postgres scaling has limits; analytical workloads belong in Delta.
- **Use as a primary copy of source-of-truth analytical data.** Land in Delta first; sync subset to Lakebase as needed.
- **Replicating Delta tables wholesale to Lakebase for "just in case" lookups.** Cost spirals; sync only what you need at low latency.
- **Heavy cross-cloud / cross-region usage.** Latency + egress.
- **Long-running analytics-style transactions.** Lock contention will surface.
- **Unbounded connection growth from a Databricks App.** Use a pooler.
- **Storing huge BLOBs in Lakebase.** Use UC Volumes for files; reference by ID.
- **Treating Autoscale as a perpetual idle dev playground without budgeting.** CoW storage on long-lived branches has cost.
- **Bypassing UC and connecting straight via Postgres credentials hardcoded in code.** UC-managed credentials + service principals are the better path.

## Weaknesses — areas Lakebase does not do well

- **Curated extension set.** Niche Postgres extensions may not be supported.
- **Synced tables are one-way.** Bi-directional needs custom logic.
- **Cross-region not native.** Add latency or replicate manually.
- **Migration between tiers requires effort.** Provisioned ↔ Autoscale is not in-place.
- **Connection caps without pooling.** Default Postgres connection limits apply.
- **Schema evolution on synced tables.** Renames / type changes break the sync.
- **Cold start (Autoscale) hurts first request.** Seconds, but still.
- **Branch storage cost can surprise.** Long-running diverged branches accumulate.
- **PITR retention has limits.** Configure based on RPO needs.
- **Lakehouse Federation back into UC adds latency for analytical queries.** Federation isn't free.
- **Limited Postgres feature parity with bleeding-edge community versions.** Some new features lag.
- **Audit + Postgres role management duality.** UC governs the instance + service identity; intra-Postgres roles are separate.

---

## Sources

- [Lakebase docs](https://docs.databricks.com/aws/en/lakebase/)
- [Provisioned Lakebase](https://docs.databricks.com/aws/en/lakebase/provisioned)
- [Autoscale Lakebase (next-gen)](https://docs.databricks.com/aws/en/lakebase/autoscale)
- [Synced tables from Delta](https://docs.databricks.com/aws/en/lakebase/synced-tables)
- [Branching and instant restore](https://docs.databricks.com/aws/en/lakebase/branching)
- [Databricks release notes overview (ai_search on synced tables, Sep 2026)](https://docs.databricks.com/en/release-notes/product/index.html)
- [Glossary](../Glossary/glossary.md)
