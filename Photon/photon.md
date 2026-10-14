# Photon

## Level 100 — What it is

Photon is Databricks' **native C++ vectorized execution engine**. It sits inside Spark's worker JVMs and takes over query execution for operators it supports, falling back to JVM Spark when it can't. You get faster SQL/DataFrame queries — Databricks cites up to 5× price/performance improvement on TPC-DS benchmarks — without changing your code.

- **Native, vectorized**: C++ + SIMD on columnar (Arrow-style) buffers.
- **Drop-in**: enabled per SKU; no code changes.
- **Partial coverage**: not all operators are Photonised; mixed plans are normal.

## Level 200 — The product surface

- **No user API**. Photon is an execution engine; you don't program against it directly.
- **Enabled on**: Photon-enabled DBR (`<ver> Photon`), most SQL Warehouses (Serverless and Pro), Serverless Jobs Compute, Serverless LDP.
- **Billing**: Photon-enabled compute has a different (slightly higher) DBU rate per SKU. Net cost typically lower because queries run faster.
- **Supported workloads**: Spark SQL, DataFrame API, Delta read/write, Lakeflow pipelines, stateless Structured Streaming (Delta, Parquet, CSV, JSON, Kafka, Kinesis sources/sinks), feature engineering. **Stateful streaming is not supported.** Accelerates Parquet writes for both Delta and Apache Iceberg.
- **Observability**: Spark UI shows Photon-executed operators highlighted; `EXPLAIN FORMATTED` shows `PhotonResultStage` / `*PhotonScan` markers.

## Level 300 — Architecture

```
Spark Logical Plan
        │
        ▼
Spark Physical Plan (with Photon transformer pass)
        │
        ▼
Stage:  ┌────────────────────────────────┐
        │ PhotonShuffleExchangeExec      │   ◄── shuffles still JVM (mostly)
        ├────────────────────────────────┤
        │ PhotonAggregate                │   ◄── Photon-native operator
        ├────────────────────────────────┤
        │ PhotonScan(delta/parquet)      │   ◄── reads stats, applies DV, columnar I/O
        └────────────────────────────────┘
                       │
                       ▼
              Arrow-style columnar batches
              processed by Photon C++ kernels
              with SIMD-accelerated paths
```

- Photon is loaded as a **native library** inside the Spark executor JVM. JNI bridges from the planner to native execution.
- The planner runs a **Photon transformer** that rewrites supported operators into their Photon counterparts.
- Mixed plans are common: an aggregate may be Photon, a downstream window function may be JVM Spark. The two sides talk via Arrow-format columnar batches with conversion overhead.

## Level 400 — Deep internals

### Operator coverage

- **Photonised** (representative): scan (Delta, Parquet), filter, project, hash aggregate, sort-merge join, broadcast hash join, hash join, sort, limit, expand, range, window (subset), shuffle exchange (read side, sometimes), expression evaluation for most primitives. Also: accelerated Parquet writes for Delta Lake and Apache Iceberg.
- **Not Photonised** (representative): some UDFs (Python row-UDF, R UDF), some non-Delta/non-Parquet sources, some grouping-set window patterns, certain decimal/string operations with edge-case unicode rules, certain expressions with Spark-only semantics, stateful streaming operators.
- Coverage expands each DBR release; release notes call out new operators.

### Mixed-plan cost

- Whenever execution crosses Photon → JVM, batches are materialised in Arrow format and the JVM reads them — non-trivial overhead.
- Best practice: rewrite Python row-UDFs as pandas UDFs (vectorised, Arrow-friendly) or pure SQL/Spark expressions to keep Photon end-to-end.

### Memory and CPU model

- Photon manages **its own memory** off-heap, separately from Spark's JVM heap. This is why you'll see Spark UI show "Photon memory" as a distinct number.
- Heavy aggregates can spill to local disk just like JVM Spark; spill format is Photon-native.
- Photon uses **SIMD** instruction sets where available (AVX2/AVX-512 on Intel, NEON on ARM/Graviton). Different instance families get different Photon throughput per core.

### Predicate pushdown + stats

- PhotonScan reads Delta `_delta_log` stats and skips files before deserialising Parquet.
- Native Parquet reader is more aggressive on column pruning, dictionary filter pushdown, and predicate vectorisation than JVM Spark's reader.
- Deletion vectors are applied inside the native scan path with negligible overhead.

### Shuffles

- Shuffle write/read is partially Photonised; the over-the-wire format is still Spark-compatible for cross-stage exchange. Photon's contribution to shuffles is mostly the serialise/deserialise paths.
- `PhotonShuffleExchangeExec` markers in the plan indicate Photon participates.

### Expression coverage

- Most arithmetic, comparison, string, and date/time expressions are Photonised.
- Some expression edge cases (locale-sensitive collation, specific decimal-precision overflow rules) escalate to JVM evaluation; rare in practice but causes "why is one column slow" surprises.
- Built-in window functions like `row_number()` over reasonable partition specs are Photonised; pathological partitions (one giant partition) hit the same skew issues as JVM Spark.

### Streaming

- Photon accelerates **stateless** Structured Streaming micro-batches: aggregations, projections, filters, and sinks for Delta, Parquet, Kafka, and Kinesis sources.
- **Stateful streaming is explicitly not supported by Photon.** Stateful operators (windowed aggregations with state, stream-stream joins with watermark, `flatMapGroupsWithState`, etc.) execute on JVM Spark regardless of Photon enablement.
- Lakeflow serverless pipelines lean heavily on Photon for stateless streaming throughput.

### Limits and gotchas

- **No Python row-UDF acceleration.** Use pandas UDFs / SQL UDFs / SQL expressions instead.
- **Cost differential.** Photon DBU rate is higher; pure scan-heavy workloads with no heavy compute don't benefit and may cost more net.
- **Engine-specific bugs.** Photon and JVM Spark sometimes have subtle semantic differences (decimal precision edge cases, error messages, certain UDF behaviours). Most are documented; surprises do happen.
- **No way to force pure-JVM in mixed plans without disabling Photon entirely.** If you need this for diagnosis, run on a non-Photon DBR/SKU and compare.
- **Photon and OSS Spark feature gap.** New OSS Spark features may not be Photon-supported on day one.

### Mental model

Photon is **"the C++ engine that quietly executes most of your SQL on Arrow buffers using SIMD"**. The mental model: assume Photon takes whatever it can, the rest stays on JVM Spark, and the boundary has cost. To get the most out of it: write SQL/DataFrame, avoid Python row-UDFs, and keep operators in the Photonised set.

---

## Anti-patterns — where Photon doesn't pay off

- **Workloads dominated by Python row-UDFs.** Photon won't accelerate them; you pay the higher Photon DBU rate for no win.
- **R or Scala UDFs that escape to JVM/R.** Same problem.
- **Pure file-listing / metadata work.** Photon helps execution, not "list 100M paths".
- **Tiny, fast queries on a serverless warehouse.** Already fast; Photon's marginal speedup may not justify SKU.
- **Workloads with massive skew you haven't fixed.** Photon won't fix algorithmic problems; one giant partition is still slow.
- **Non-Delta/non-Parquet sources with limited Photonised scans.** CSV / JSON read paths are less Photonised; minor net gain.
- **Heavy decimal-precision-edge logic.** Some decimal cases escalate; mixed-mode overhead may dominate.
- **Treating Photon as a fix for bad query design.** Selectivity and joins still matter more than engine.
- **Choosing Photon for streaming with all-UDF transforms.** Engine doesn't help; rewrite the UDFs first.
- **Photon on tiny single-node ML training.** It's a SQL/DataFrame engine, not an ML accelerator.

## Weaknesses — areas Photon does not do well

- **Python row-UDFs.** Hard ceiling; must rewrite as pandas/SQL UDFs.
- **Mixed-plan overhead.** Crossing the JVM/native boundary costs.
- **Operator coverage is partial.** Some patterns silently drop out to JVM Spark.
- **Higher DBU rate.** Net positive only if your workload actually benefits.
- **Semantic differences from JVM Spark.** Rare but real; can break test snapshots.
- **No OSS / community Photon.** Closed-source; you can't extend it.
- **Observability is opaque-ish.** Spark UI shows Photon stages but profiling at the operator level is harder than OSS Spark.
- **No GPU codepath.** Photon is CPU/SIMD. For GPU acceleration of SQL, separate path (RAPIDS-on-Spark, etc.).
- **Tuning knobs are few.** Photon mostly auto-tunes; if it's slow, options are limited.
- **DBR coupling.** Photon version is tied to DBR; can't upgrade Photon independently.
- **Some Delta features lag.** New Delta protocol features may not be Photon-accelerated immediately.
- **Stateful streaming not supported.** Photon does not accelerate stateful operators; they run on JVM Spark. Plan for JVM performance on any stateful streaming work.

---

## Sources

- [Photon docs](https://docs.databricks.com/aws/en/compute/photon)
- [Photon: a fast query engine for lakehouse systems (paper, SIGMOD 2022)](https://www.databricks.com/wp-content/uploads/2022/07/photon-sigmod22.pdf)
- [Photon-enabled DBR](https://docs.databricks.com/aws/en/runtime/photon)
- [Glossary](../Glossary/glossary.md)
