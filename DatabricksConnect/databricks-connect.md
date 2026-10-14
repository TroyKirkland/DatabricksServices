# Databricks Connect

## Level 100 — What it is

Databricks Connect lets you write and run Spark code on your **local IDE** (PyCharm, VS Code, Cursor, plain Python) that executes against a **remote Databricks cluster**. It's a thin client; the cluster does the work. Built on the open-source Spark Connect protocol.

- **Local dev, remote compute**: edit and debug in your IDE, run on a real cluster.
- **Spark Connect under the hood**: no more JVM on your laptop.
- **Same DataFrame API**: `pyspark.sql` works identically to in-notebook code.

## Level 200 — The product surface

- **Python, R, and Scala** clients (current generation).
- **Auth modes**: OAuth U2M (interactive), OAuth M2M (service principal), PAT.
- **Target compute**:
  - All-purpose cluster (classic) — pick a specific cluster ID.
  - Serverless compute — no cluster ID; Databricks picks.
  - SQL warehouse — for SQL-only flows (different code path).
- **Versioning**: Databricks Connect Python package version pins to the DBR (e.g., `databricks-connect==16.4.*` for DBR 16.4). Matching is strict for Python; minor mismatches can be tolerated.
- **Bundled with**: `pyspark` namespace replacement, `databricks.sdk` for workspace APIs, MLflow integration.
- **Entry points**: `DatabricksSession.builder` (replaces `SparkSession.builder`).
- **IDE integrations**: VS Code "Databricks" extension makes connection setup + cluster selection one-click.

## Level 300 — Architecture

```
[Your laptop/IDE]                                       [Databricks workspace]
  Python interpreter                                       │
    │                                                      │
    pyspark / DataFrame API                                │
    │                                                      │
    DatabricksSession (Spark Connect client)               │
    │                                                      │
    gRPC over HTTPS  ──────────────────────────────►  Spark Connect server
                                                          (Spark driver on cluster)
                                                              │
                                                              ▼
                                                          Executors → UC tables
```

- **Spark Connect**: open protocol. Client sends an unresolved logical plan; server resolves, optimises, executes; streams results back.
- **No JVM locally**: your laptop just builds plans and serialises them. All execution is server-side.
- **Same auth chain as the workspace**: U2M/M2M/PAT determine the run-as identity → UC permissions enforced server-side.

## Level 400 — Deep internals

### Wire protocol

- gRPC streaming over HTTPS to `<workspace-url>/api/2.0/spark/protobuf/` (or equivalent endpoint).
- Each `DataFrame` operation appends to a logical plan; only **actions** (`collect`, `count`, `show`, `write`) trigger a request.
- Results stream back as Arrow IPC; the client materialises into pandas/Arrow.

### Version pinning

- The Python `databricks-connect` package is pinned to a DBR version. The cluster's DBR must match the client's pin (minor mismatch sometimes tolerated, major mismatch generally not).
- Updating the cluster's DBR requires re-installing the matching `databricks-connect`.
- Serverless decouples this somewhat: Databricks-managed runtime version is hidden, and the client needs a compatible Spark Connect protocol version.

### Auth + identity

- OAuth U2M: interactive browser flow; refresh token cached locally.
- OAuth M2M: service principal client credentials; ideal for CI/CD.
- PAT: legacy; still works.
- The remote runs as that identity → UC permission checks resolve against it.

### Local-remote considerations

- `collect()` ships data over the wire — be careful with large DataFrames.
- Local Python objects in closures must be serialisable; Spark Connect ships them to the cluster.
- Files on your laptop are not on the cluster; you cannot `spark.read("local/file.csv")`. Use UC volumes or upload via the SDK first.
- `dbutils` is partially available; some methods are server-side only.

### Single-user vs shared clusters

- For UC-governed code, prefer **single-user (assigned) clusters** to get full UC semantics.
- Shared clusters work but have restrictions on certain UDF / RDD paths.

### Differences from notebook execution

- No magic commands (`%sql`, `%pip`, `%run`); use Python `spark.sql(...)`, `subprocess` for pip, or the notebook-only equivalents.
- `display()` becomes `df.show()` (or use pandas/Arrow conversion).
- Long-running cells: a normal Python script handles them fine; IDE timeouts matter.

### Failure modes

- Cluster not running: the client auto-starts the cluster (if permissions allow); first call is slow.
- Cluster terminated mid-call: the operation fails; reconnect succeeds.
- Network blip: gRPC retries within a stream; long ops can fail.
- Authentication expiry: refresh token expires; re-auth via `databricks auth login`.

### Limits / realities

- One `DatabricksSession` per script is the norm; switching clusters requires a new session.
- Some PySpark APIs that depend on JVM internals (`SparkContext._jvm.something`) don't work — there's no local JVM.
- Some RDD APIs are deprecated or unavailable; DataFrame is the supported surface.
- Large UDFs with heavy local dependencies (closure captures) can be slow to ship.
- Streaming via Databricks Connect works but is less common; production streaming is usually a job.

### Mental model

Databricks Connect is **"the cluster is your Python kernel, your laptop is the editor"**. The same DataFrame code that runs in a notebook now runs from your IDE — with proper version control, breakpoints, type checking, and unit tests around it.

---

## Anti-patterns — where you should not use Databricks Connect

- **Production batch jobs.** Use Lakeflow Jobs running on the cluster, not a developer's laptop process.
- **Long-running production streams.** Same reason; an IDE session isn't durable.
- **Code that depends on local files on the laptop.** Cluster can't see them. Upload to a UC volume first.
- **Heavy `collect()` workflows.** Each collect ships data over WAN to your laptop. Aggregate first.
- **Connecting to a different DBR than the client pin.** Either match versions or use serverless.
- **JVM-internals tricks (`_jvm`, `_jsc`, etc.).** No JVM locally; these are gone.
- **Running on a heavily shared all-purpose cluster.** Noisy neighbours + your dev queries don't mix well.
- **Networks without HTTPS to Databricks.** No alternative transport.
- **Replacing notebooks entirely.** Notebooks remain better for interactive exploration with `display()`, magics, etc.
- **CI/CD with U2M auth.** Use M2M (service principal) for non-interactive runs.

## Weaknesses — areas Databricks Connect does not do well

- **Version pinning friction.** Client version must match DBR; team-wide DBR upgrades require synchronized client upgrades.
- **No `display()`.** Notebook-style rich rendering is gone; use `show()` or convert to pandas.
- **No `%pip`, `%sql`, `%run`.** Notebook magics aren't supported.
- **Some `dbutils` surface is server-only.** Test which methods you actually need.
- **Cold cluster start is on the critical path.** Pools or serverless mitigate.
- **Closure serialization can surprise you.** Local objects you didn't expect to be shipped get shipped — and may fail to deserialise on the cluster.
- **Spark UI / debugging lives on the cluster.** Not in your IDE. Click through to the Spark UI to investigate.
- **Streaming workflows are awkward.** A `start()` call doesn't naturally produce an interactive feedback loop in an IDE.
- **No first-class unit-test runner.** You can write unit tests but you need to manage session lifecycle yourself.
- **Latency floor for tiny queries.** Each gRPC round trip is tens of ms; chatty code feels slow.
- **Doesn't replace asset bundles.** For deployable code, you still need DABs / wheels / job definitions.
- **R and Scala support trail Python.** Python is the most mature client; R and Scala have language-specific docs and smaller feature surface.

---

## Sources

- [Databricks Connect docs](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/)
- [Databricks Connect for Python](https://docs.databricks.com/aws/en/dev-tools/databricks-connect/python)
- [Spark Connect (OSS)](https://spark.apache.org/spark-connect/)
- [VS Code extension](https://docs.databricks.com/aws/en/dev-tools/vscode-ext/)
- [Glossary](../Glossary/glossary.md)
