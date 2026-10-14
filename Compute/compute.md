# Compute on Databricks

> Cross-cutting topic doc. Synthesizes [[glossary:Databricks Runtime (DBR)]], [[glossary:Photon]], [[glossary:Cluster]], [[glossary:Serverless compute]], [[glossary:SQL Warehouse]], [[glossary:Databricks Connect]], and the runtime knobs that decide cost and latency.

## Level 100 — What it is

Compute on Databricks is **Spark (with Photon acceleration) running on VMs that you either own (classic) or that Databricks owns and pre-warms (serverless), exposed via four product shapes**: all-purpose clusters, jobs clusters, SQL warehouses, and Model Serving endpoints. You don't manage a Spark cluster manifest. You pick a shape, a size, and a runtime — the platform does the rest.

- **Two stewardship models** — *classic* (VMs in your VPC/VNet, you pay cloud + DBU) vs *serverless* (VMs in Databricks' account, you pay only DBU at a higher rate).
- **One execution engine, two implementations** — JVM Spark and [[glossary:Photon]] (C++ vectorized). Photon engages automatically for supported operators.
- **Compute is billed in [[glossary:DBU (Databricks Unit)]]** — different shapes (Jobs, All-Purpose, SQL, LDP, Serverless, Model Serving) have different DBU rates.

## Level 200 — The product surface

| Shape | What it's for | Classic? | Serverless? |
|---|---|---|---|
| **All-purpose cluster** | Interactive notebooks, ad hoc work | yes | yes (notebook serverless) |
| **Jobs cluster** | One run, then teardown — [[glossary:Lakeflow Jobs]] tasks | yes | yes |
| **SQL Warehouse** | SQL-only compute, BI/dashboards/Statement Execution API | yes (Pro/Classic) | yes (Serverless SQL) |
| **LDP pipeline compute** | [[glossary:Lakeflow Spark Declarative Pipelines]] runs | yes | yes (default for new pipelines) |
| **Model Serving endpoint** | Real-time inference (CPU/GPU, scale-to-zero) | — | yes |

Core knobs on a cluster:

- **Databricks Runtime version** — pinned per cluster. Current LTS lineup: 18 LTS (Spark 4.1, JDK 21), 17.3 LTS (Spark 4.0), 16.4 LTS (Spark 3.5). ML and GPU variants track the same numbering. Bundle of Spark + Photon + libs + Delta version.
- **Node type** — cloud VM family (general / memory / compute / GPU). Driver and workers can differ.
- **Autoscaling** — min/max workers; enhanced autoscaling on jobs/LDP rebalances mid-run.
- **Photon** — toggle (on for serverless and most modern classic clusters; engages where supported).
- **Access mode** — *Standard* (multi-user UC-aware), *Dedicated* (single user or group, formerly "Single user"), *No isolation* (legacy, avoid).
- **Spot mix** (classic only) — spot/on-demand ratio for workers; driver typically on-demand.
- **Pools** — pre-warmed [[glossary:Pool]] of idle VMs the cluster can draw from to skip cold start.
- **Init scripts** — workspace-scoped shell scripts that run on each node at startup. Classic only; serverless does not support them.
- **Cluster policy** — admin-defined template constraining what users can spin up (node types, runtime, autoscaling bounds, tags).
- **Dependency mode** (beta) — manages libraries at the workload level using base environments, decoupling library state from cluster config.
- **Local disk encryption** (public preview) — encrypts shuffle data on local disks for regulated workloads (classic clusters, AWS).
- **AWS Capacity Blocks** — reserve compute capacity for burst GPU/CPU workloads on AWS classic clusters.

SQL Warehouse specifics:

- **Serverless** (default) — fastest start (seconds), elastic scaling, shared multi-tenant fleet, Photon always on.
- **Pro** — single-tenant in your VPC, supports certain regulated workloads and private link, slower start (~2-5 min).
- **Classic** — legacy single-tenant; new deployments should prefer Pro or Serverless.

Authoring:

- **UI** — cluster create page; SQL Warehouse create page.
- **API / Terraform / DAB** — clusters, warehouses, jobs, pipelines all declarable in [[glossary:Databricks Asset Bundle]] YAML.
- **[[glossary:Databricks Connect]]** — local IDE talks to a remote cluster/warehouse over [[glossary:Spark Connect]]. The default modern dev loop.

## Level 300 — Architecture

```
                     ┌────────────────── Control plane (Databricks) ──────────────────┐
                     │  Workspace UI / REST API / Jobs scheduler / Driver-proxy / Auth │
                     └───────┬─────────────────────────────────────────────────┬──────┘
                             │ classic                                         │ serverless
                             ▼                                                 ▼
                ┌──────────── Customer VPC/VNet ───────────┐          ┌──────────── Databricks-owned account ──────────┐
                │ Driver VM   (DBR = Spark + Photon + libs)│          │  Multi-tenant pre-warmed fleet                  │
                │   ├── Spark driver JVM                   │          │   ├── Driver pod (per workload, isolated)       │
                │   ├── Cluster manager (Databricks)       │          │   ├── Executor pods                             │
                │   └── Init scripts (optional)            │          │   ├── NAT egress / PrivateLink ingress (UC)     │
                │ Worker VMs (1..N, autoscale)             │          │   └── Per-tenant network namespaces             │
                │   ├── Executors (JVM)                    │          └────────────────┬────────────────────────────────┘
                │   └── Photon native processes            │                           │
                └──────────────────┬───────────────────────┘                           │
                                   │                  Reads/writes via UC short-lived creds
                                   ▼                                                   ▼
                                       ┌──────── Cloud object storage (Delta) ─────────┐
                                       └───────────────────────────────────────────────┘
```

**Two-plane model.** The *control plane* (workspace, UI, scheduler, auth, query history, UC) lives in Databricks' account. The *data plane* (driver + executors that actually run your code) lives either in your account (classic) or Databricks' account (serverless). UC mediates all storage access via short-lived credentials regardless of plane.

**Spark execution model**:

- The **driver** parses code into a logical plan, optimizes via Catalyst + AQE, schedules tasks against executors.
- **Executors** are JVMs (with Photon native sidecars when enabled) that run tasks. Each task processes one partition.
- Shuffles redistribute data between stages via local disk + network.
- AQE (Adaptive Query Execution) rewrites the physical plan at runtime — coalesces partitions, switches join strategies, handles skew.

**Photon** is not "fast Spark." It is a *separate* execution engine: C++, vectorized, columnar, SIMD-friendly. The driver decides per-operator whether to dispatch to Photon or fall back to JVM Spark. Photon is most decisive on wide scans, aggregations, joins, and Delta reads (deletion vectors, stats-based pruning are native). UDFs in pure Python, complex Pandas operations, and a few exotic operators still fall back. See companion doc [[../Photon/photon.md]].

**Autoscaling**:

- **Standard autoscaling**: scale up when pending tasks exceed capacity for a threshold time; scale down when idle.
- **Enhanced autoscaling** (Jobs, LDP): proactive, can scale down mid-stage; tuned for batch.
- **Serverless SQL**: cluster-of-clusters — adds *warehouses* (replicas) when concurrent query queue grows, not just workers.

**Access modes and UC**:

- **Standard** (formerly "Shared") — multiple users on one cluster, UC enforced. Photon supported. **Required** for full UC features like row filters and column masks on Spark.
- **Dedicated** — single user (or single group); UC enforced. Allows things Standard doesn't (RDD APIs, Scala on some configs, some legacy operations).
- **No isolation shared** — legacy, no UC, deprecated.

**Cost model**:

- Compute cost = **(VM cost paid to cloud) + (DBU rate × DBU consumption × workload-specific multiplier)**.
- Serverless = DBU-only (Databricks eats the VM cost — DBU rate is higher to compensate).
- Photon doubles the DBU multiplier on classic clusters but typically pays for itself via shorter runtime.

## Level 400 — Deep internals

### What `Databricks Runtime` actually contains

- A **specific Spark build** (often ahead of OSS Spark; backported fixes, Databricks features like Delta-specific optimizations).
- **Photon** native libraries.
- **Delta** version pinned to the runtime.
- **JDK** version pinned (JDK 21 on DBR 18 LTS; JDK 17 on DBR 16.x / 17.x).
- **Pre-installed Python / Scala / R / Java libraries** with version pins. The ML runtime adds CUDA, PyTorch, scikit-learn, etc. The GPU runtime adds the driver stack.
- **Cluster manager binaries** — Databricks' own scheduler talking to the control plane.
- **System libs** — JVM, native compression libs (zstd, lz4), networking.

LTS releases get bugfix/security patches for ~2–3 years (DBR 18 LTS supported through June 2029). Non-LTS get 6 months. Pinning a workload to a specific LTS is the responsible default for production jobs. DBR 18 LTS also introduces SQL Scripting (procedural SQL) as GA and state repartitioning for stateful streaming.

### Photon dispatch in detail

Photon sits beside Spark in the executor process. The query planner annotates each physical operator with a Photon-supported flag. At execution:

1. If every operator in a *stage* is Photon-supported, the whole stage runs in Photon — columnar batches of `~4096` rows pass between operators.
2. If a stage mixes Photon and JVM operators, data crosses the JNI boundary as Arrow record batches. This costs CPU; the planner avoids it when it can.
3. **What Photon accelerates well**: Parquet scan + decode, filters, projections, aggregations, hash joins, sort, window with simple frames, Delta reads (DVs, stats), most SQL built-ins.
4. **What Photon does not handle**: Python UDFs (run in Python worker as before), `mapInPandas`/`applyInPandas` (Pandas vectorized — also outside Photon), some narrow operators (`explode` on certain types), RDD APIs, certain rare expression combinations. Mixing these forces row-by-row across the boundary — a perf cliff.
5. Photon has **its own memory manager** (off-heap, separate from JVM heap). Sizing the cluster requires accounting for both. Memory-pressure spills go through Photon's spill manager.

### Adaptive Query Execution (AQE)

- **Coalesce shuffle partitions**: AQE looks at shuffle outputs and merges small partitions to reduce task overhead.
- **Dynamically switch join strategies**: a sort-merge join may flip to broadcast hash join if one side turns out small after the first stage runs.
- **Skew join handling**: detects oversized partitions in a join key and splits them into sub-partitions.
- **Local shuffle reader**: avoids re-reading shuffles already on local disk.

AQE is on by default since DBR 10+. Disabling it is almost always a mistake.

### Dynamic File Pruning (DFP)

When a small dimension joins a big fact, DFP pushes the dimension's filter values into the fact scan after the first probe — pruning Parquet files at the *scan* level. This is one of the bigger wins from running on Databricks vs. vanilla Spark, and Photon makes it cheaper.

### Disk cache (legacy "Delta cache")

- SSD-backed cache of decoded Parquet pages on worker local disk.
- Used automatically by classic clusters with cache-enabled instance types.
- **Serverless doesn't expose a user-visible disk cache** — caching happens at lower layers (block cache on shared NVMe, OS page cache on shared workers). The user-tunable knob is gone.
- Cache is per-cluster, lost on restart. Not a substitute for warm SQL Warehouse routing.

### Cluster startup, end to end (classic)

1. User clicks **Start**. Control plane allocates cloud VMs via the cloud provider API.
2. VMs boot from a Databricks-prepared AMI/image. The runtime image is downloaded (or cached on the AMI).
3. **Init scripts** run on every node before Spark starts. Workspace-scoped scripts only; legacy cluster-scoped scripts are deprecated.
4. Driver Spark process starts; workers register.
5. UC token agent starts. Cluster is "Running."

Total: 2–6 minutes typical, mostly VM boot + image fetch. Pools eliminate VM-allocation latency by holding pre-booted VMs; init scripts still run on attach.

### Serverless internals (mental model only — exact impl is opaque)

- **Pre-warmed fleet**: Databricks holds VMs at the ready in their account, per region.
- **Per-workload isolation**: each customer workload runs in its own driver + executor pods inside an isolated network namespace. Cross-tenant access is impossible at the network layer.
- **Egress / private link**: outbound traffic NATs through Databricks-managed egress IPs; you can add an allowlist via Network Connectivity Configuration (NCC). Customer-managed PrivateLink ingress is supported for serverless SQL on some clouds.
- **Cold start**: typically 5–15 seconds for SQL Warehouse, sub-minute for Jobs/LDP. Far faster than classic.
- **Trade-offs**: no init scripts, no custom JARs (use library management at the Job/Pipeline level), no Spark-config carte blanche (subset of Spark configs allowed), some libraries can't be installed cluster-wide.

### SQL Warehouse internals

- Always Photon on.
- **Serverless** routes queries to a fleet of warm "clusters" within the warehouse. Each warehouse has a *cluster size* (e.g., Small = 1 cluster of fixed worker count) and a *cluster scaling* range (min..max clusters). Concurrent queries route to clusters; the autoscaler adds clusters when the queue grows.
- A **single query never spans clusters** — sizing matters when one query is the bottleneck. Multiple clusters help concurrency, not single-query latency.
- **Query history & profiles** in the workspace UI are the primary debugging surface — they show physical plan, stages, scanned bytes, partitions read, photon engagement, AQE rewrites.

### Spark Connect / Databricks Connect

- [[glossary:Spark Connect]] is an open-source protocol: client builds an unresolved logical plan, ships it to the server, server compiles and runs.
- [[glossary:Databricks Connect]] is the Databricks client for this protocol — talks to any modern cluster or SQL warehouse.
- Implications: client process doesn't need a JVM. Pure Python `pyspark` on the client. Notebooks behave the same whether run in workspace or via Connect.

### DBUs, billing, and where the money goes

- Per-second billing, rounded up to per-second on serverless.
- **All-purpose** DBU rate > **Jobs** rate > **LDP** rate (varies). Picking the right shape matters more than micro-tuning Spark configs.
- **Photon-on classic** doubles the DBU multiplier per worker. Almost always worth it; the wall-clock reduction pays for the rate increase.
- **Spot worker mix** reduces cloud cost; doesn't change DBU rate. Spot reclaim during a job triggers task retry — fine for stateless work, painful for long shuffles.
- **`system.billing.usage`** is the source of truth for retrospective cost analysis. Cluster tags propagate to billing for chargeback.

### Concurrency, isolation, fault tolerance

- **Driver failure** is fatal — the cluster restarts. Mitigation: jobs cluster (one-shot, automatic retry at the Job level); Lakeflow Jobs `max_retries`.
- **Executor failure** is recoverable — Spark re-runs lost tasks. Repeated executor losses suggest spot reclaim or OOM.
- **Standard access mode** isolates users via UC permissions, but a runaway query from one user can starve compute for others on the same cluster — sizing or Workload Management Profiles (where available) help.
- **Network egress** from classic clusters goes via your VPC's NAT/IGW; serverless via Databricks' egress (with optional NCC allowlist).

### Hard limits & gotchas

- Max executors per cluster is high (thousands) but autoscale-up to that count rarely helps — you bottleneck on shuffle or storage IOPS first.
- Init scripts that take >10 minutes will time out and fail cluster start. Move heavy setup into Docker images (where supported) or libraries.
- DBR upgrades are not backward-compatible for every Spark API — pin LTS in production, validate in staging on the new version, then move.
- Serverless config allowlist is smaller than classic — Spark configs you rely on (e.g., custom `spark.sql.shuffle.partitions` extremes, JVM tuning) may not be settable.
- Python UDF performance is the most common perf surprise — they round-trip data between Photon → JVM → Python worker. Use built-in SQL functions or Pandas UDFs (`mapInPandas`) where possible.
- Pool warmup is not instant — a cold pool takes a couple of minutes to populate. Pools help steady-state startup latency, not the first job after a quiet weekend.

### Mental model

Compute on Databricks is **"pick the shape (jobs / all-purpose / SQL / serving), pick the stewardship (classic vs serverless), pick the runtime, then trust the scheduler."** Beneath that, Spark + Photon + AQE + DFP are the same machinery everywhere — they just live in different places. Most performance work is *less Spark tuning, more shape choice + Photon coverage + data layout* (clustering, file size, stats). When something is slow, ask in this order: *is this the wrong shape (interactive job on all-purpose, batch job on serverless SQL) → is the data laid out badly (small files, no clustering) → is Photon falling back (Python UDFs, exotic ops) → is shuffle blowing up (skew, undersized cluster)?*

---

## Anti-patterns — where you should not use Databricks compute like this

- **All-purpose clusters for scheduled jobs.** All-purpose DBU rate is the most expensive. Use a Jobs cluster (or serverless Jobs).
- **Long-lived "shared" clusters that everyone attaches to.** Noisy-neighbor city, and you pay 24/7 even when no one's working. Serverless notebooks or per-user warehouses are the modern answer.
- **Classic SQL Warehouse for new deployments.** Pro or Serverless. Classic is legacy.
- **Tiny SQL Warehouse for BI dashboards.** A 2X-Small struggles under any meaningful concurrency. Right-size or rely on cluster scaling.
- **Spark for sub-second OLTP.** Wrong tool. Lakebase, Model Serving, or a dedicated KV store.
- **Init scripts to install heavy native libraries.** Slows every cluster start. Bake into a Docker image (where supported) or use cluster libraries.
- **Disabling Photon on a workload Photon would accelerate.** Often done to "save money" — usually costs more in wall-clock.
- **Python UDFs in hot paths.** Pull the logic into Spark SQL, expression DSL, or Pandas UDFs.
- **Pinning to a non-LTS DBR in production.** Six-month support window; you'll get forced upgrades on someone else's schedule.
- **Single huge cluster for a workload that fans out into many small queries.** Use a SQL Warehouse with cluster scaling — concurrency comes from multiple clusters, not one bigger cluster.
- **Notebook-scoped libraries for a job.** Each task installs them at startup; pin cluster libraries or DAB-managed environments.
- **Running model inference on a Spark cluster instead of Model Serving.** Spark is bad at low-latency single-row inference. Model Serving exists for this.

## Weaknesses — areas Databricks compute does not do well

- **Sub-second query latency at low concurrency** — even serverless SQL has a few hundred ms of overhead. Below ~100ms is not the design point.
- **Hard real-time streaming.** Structured Streaming micro-batches at seconds. Use Kafka + Flink or specialized engines for ms-level SLAs.
- **Custom Spark distributions / forks.** You run Databricks' Spark, period. OSS users sometimes hit features that aren't backported until later.
- **GPU workloads on serverless are limited.** Serverless GPU exists for Model Serving and some notebook serverless contexts, not parity with the classic GPU runtime.
- **Init-script ecosystems don't translate to serverless.** Migrating a classic workload heavy in init scripts to serverless is real work.
- **Driver-side bottlenecks** for plans with millions of files, huge query plans, or many UDF closures. The driver becomes the limit; throwing executors doesn't help.
- **JVM tuning is mostly off-limits.** Heap size on the driver is configurable; deep JVM flags are not user-tunable on most modern shapes.
- **Cross-region compute** is not a thing — a cluster lives in one region. Reading another region is egress + latency.
- **Pool warmup latency** under bursty workloads — a quiet pool takes time to repopulate.
- **Spot interruptions** during shuffle-heavy stages cause big retries. Spot for cheap ETL is great; spot for a 3-hour shuffle-bound job is risky.
- **Photon JNI boundary** dominates the cost when workloads mix native and JVM ops. Sometimes "more Spark, less Photon" is faster than mixed — observed but counterintuitive.
- **Library dependency hell** on the ML runtime — pinned versions of PyTorch/Transformers conflict with what users want to install. Custom containers are an escape but limited to certain shapes.

---

## Sources

- [Databricks Runtime release notes](https://docs.databricks.com/aws/en/release-notes/runtime/)
- [DBR 18 LTS release notes](https://docs.databricks.com/aws/en/release-notes/runtime/18.html)
- [Compute configuration reference](https://docs.databricks.com/aws/en/compute/configure)
- [Access modes](https://docs.databricks.com/aws/en/compute/access-mode-limitations)
- [Photon](https://docs.databricks.com/aws/en/compute/photon)
- [SQL Warehouses](https://docs.databricks.com/aws/en/compute/sql-warehouse/)
- [Serverless compute](https://docs.databricks.com/aws/en/compute/serverless/)
- [Databricks Connect](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/)
- [Pricing & DBU rates](https://www.databricks.com/product/pricing)
- [Adaptive Query Execution (AQE)](https://docs.databricks.com/aws/en/optimizations/aqe)
- [Dynamic File Pruning (DFP)](https://docs.databricks.com/aws/en/optimizations/dynamic-file-pruning)
- Companion product docs: [Databricks Runtime](../DatabricksRuntime/databricks-runtime.md), [Photon](../Photon/photon.md), [Databricks Connect](../DatabricksConnect/databricks-connect.md), [Databricks SQL](../DatabricksSQL/databricks-sql.md), [Lakeflow Jobs](../LakeflowJobs/lakeflow-jobs.md), [Mosaic AI Model Serving](../MosaicAIModelServing/mosaic-ai-model-serving.md)
- [Glossary](../Glossary/glossary.md)
