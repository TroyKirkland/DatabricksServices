# Databricks Runtime (DBR)

## Level 100 — What it is

Databricks Runtime is a **versioned bundle** of Apache Spark + Photon + Delta Lake + cloud SDKs + ML libraries + Java/Python/Scala/R runtimes, packaged as a single image you pick when you create a cluster. It's "the OS for Databricks compute".

- **Versioned**: each DBR (e.g., 15.4 LTS, 16.x) pins specific versions of Spark, Photon, Delta, Java, Python.
- **Variants**: standard, **ML** (adds PyTorch/TF/transformers/etc.), **GPU** (CUDA + GPU-optimised libraries).
- **LTS**: Long-Term Support releases get ~3 years of fixes; non-LTS releases get ~6 months.

## Level 200 — The product surface

- **DBR versions** chosen at cluster create time (e.g., `18 LTS`, `19`, `19 ML`, `19 ML GPU`). Current supported LTS versions: 18 LTS (Spark 4.1.0), 17.3 LTS (Spark 4.0.0), 16.4 LTS (Spark 3.5.2), 15.4 LTS (Spark 3.5.0), 14.3 LTS (Spark 3.5.0). Latest non-LTS: 19 (Spark 4.2.0).
- **Bundled**: Spark, Photon, Delta, cloud SDKs (boto3, azure-sdk, google-cloud-*), commonly-used Python libs (pandas, pyarrow, scikit-learn), `dbutils`, MLflow.
- **ML runtime adds**: PyTorch, TensorFlow, Hugging Face transformers, sentence-transformers, LightGBM, XGBoost, scikit-learn extras, conda env preconfigured.
- **GPU runtime adds**: CUDA + cuDNN, NCCL, GPU-aware ML libs.
- **Init scripts**: shell scripts run at cluster startup to install extra packages or configure the OS. Cluster-scoped or UC-volume-scoped.
- **Cluster libraries**: PyPI / Maven / CRAN packages added via UI/API; installed on every node.
- **Notebook-scoped libraries**: `%pip install` inside a notebook scopes pkgs to that notebook.
- **Compute policies**: admin-defined templates that lock down which DBR versions/instance types users can pick.
- **Serverless equivalent**: serverless compute (jobs, notebooks, SQL warehouses, LDP) uses a Databricks-managed equivalent runtime; you don't pick a DBR version, you pick a serverless tier.

## Level 300 — Architecture

```
Cluster create:
  ┌─ Cloud VM (your account on classic, Databricks' on serverless)
  │   ├─ Databricks Container Service / DBR rootfs
  │   │   ├─ Spark + Photon (JVM)
  │   │   ├─ Delta + open-format libs
  │   │   ├─ Python / R / Scala interpreters
  │   │   ├─ ML libs (ML runtime)
  │   │   ├─ CUDA + NCCL (GPU runtime)
  │   │   └─ dbutils + Databricks SDKs
  │   ├─ Init scripts run
  │   ├─ Cluster libraries installed
  │   └─ Driver / executor processes start
  └─ Networking: VPC, security groups, instance profile / managed identity attached
```

- **Driver + executors** are JVM processes; Python uses Py4J + Arrow for cross-process data movement.
- **Photon** is loaded as a native library inside the Spark executor; for supported operators it replaces JVM evaluation.
- **Local disk** under `/local_disk0` is ephemeral instance store; DBFS / UC volumes are durable.
- **DBR rootfs** is built from a base image; updates roll via DBR version bumps.

## Level 400 — Deep internals

### Versioning and support

- **Major.minor** versioning, e.g. 15.4. Minor releases roughly every 4-6 months.
- **LTS** every few majors; supported 36 months (3 years) minimum. Non-LTS: ~6 months.
- DBR version pins: a specific Spark commit, a Photon version, a Delta protocol, JVM (Java 21 since DBR 18; Java 17 on earlier versions), Python (3.10+; 3.12+ on DBR 18+), Scala 2.13 (as of DBR 18; 2.12 on earlier).
- Behavior changes across major bumps are documented in the release notes; assume some breakages between non-LTS releases.

### Container architecture

- DBR rootfs is essentially a hardened Ubuntu-based image baked with Spark + Photon + libs.
- Classic: rootfs runs inside cloud VMs (EC2/Azure VM/GCE) provisioned in your VPC.
- Serverless: runtime is a Databricks-managed equivalent, locked down. No init scripts, restricted library install, no SSH.
- Databricks Container Services (DCS) lets you bring your own Docker image based on a Databricks base — for cases where the standard DBR doesn't fit.

### Photon engagement

- Loaded automatically on Photon-enabled SKUs.
- Operates as a C++ native engine reading Parquet/Delta files into Arrow-style columnar buffers, executing predicates, projections, aggregations, joins on supported operators.
- Falls back to JVM Spark for unsupported operators (some UDFs, certain window patterns, some non-Delta sources). Mixed plans are normal.

### Library precedence

- Bottom-up: DBR-bundled libs → cluster libraries → notebook-scoped (`%pip install`).
- `%pip install` in a notebook re-installs the package across the cluster Python venv for that notebook scope; subsequent notebooks see the original env.
- Init scripts run as root before Spark starts; useful for OS-level packages, certificates, agent installation.
- UC-scoped init scripts are recommended over DBFS-scoped (deprecation path for DBFS init scripts).

### dbutils

- The `dbutils` namespace exposes utilities that bridge to the workspace control plane: secrets, widgets, fs, jobs, taskValues, notebook (run another notebook), library.
- Python `dbutils` is implemented via Py4J calls to the JVM driver.

### Cluster lifecycle

- **States**: pending, running, restarting, resizing, terminating, terminated.
- **Auto-termination**: idle clusters die after N minutes; saves cost.
- **Pool**: pre-warmed VMs from which clusters acquire workers — skip cold start at cost of running idle pool.
- **Autoscaling**: standard or **enhanced** (for jobs / LDP). Enhanced releases workers more aggressively after a stage finishes.

### Networking

- All cluster VMs land in the workspace's VPC/VNet.
- Egress to source-of-truth: Databricks control plane, cloud storage, optional Private Link.
- Per-cluster security: instance profile (AWS) / managed identity (Azure) / service account (GCP) provides cloud auth; UC additionally enforces fine-grained access.

### Limits / realities

- Adding lots of libraries via cluster libraries increases startup time dramatically. Bake into a custom DCS image if you have a long list.
- Non-LTS runtimes can break breaking SDKs / your code. LTS is safer for prod.
- `%pip` reinstalls every command run; combine into one `%pip install pkg1 pkg2 pkg3`.
- ML runtime adds significant cold-start time vs standard.
- GPU runtime requires GPU-class instance types; using GPU code on non-GPU instances silently runs on CPU.
- Serverless has no init scripts and a restricted library set; bake into your notebook with `%pip` or use classic.

### Mental model

DBR is **"the immutable OS image for a cluster"**. Pick a version, pick a variant (standard / ML / GPU), maybe layer init scripts or DCS for special needs. Long-term decisions: stay on LTS for production, use ML when you need DL/transformers, use GPU only when actually GPU-bound.

---

## Anti-patterns — where you should not pick a non-default DBR

- **Latest non-LTS in production.** Cool features, breakage risk. Use LTS for prod, latest for dev/exploration.
- **GPU runtime for non-GPU code.** Pays GPU instance + GPU runtime overhead; gets CPU performance.
- **ML runtime for non-ML workloads.** Cold start is slower; libraries you don't use add attack surface.
- **Heavy init-script library installs.** Slows cluster start dramatically; switch to DCS image or stay on serverless.
- **Multiple DBR versions across a team.** Reproducibility nightmare. Standardise via cluster policies.
- **Running ML training on shared all-purpose clusters.** Resource contention. Use job clusters with autoscale or single-user clusters.
- **GPU clusters left running for batch ML.** Expensive. Use job clusters with auto-termination or scale-to-zero serverless.
- **Pinning random Python packages with version specs that conflict with DBR-bundled versions.** Causes mysterious runtime failures. Match DBR's Python version and bundled package versions.
- **Treating notebook-scoped libs as durable.** They evaporate when the notebook session ends. Cluster libs or DCS for durable installs.
- **Using DBFS init scripts.** Deprecated in favor of UC-volume init scripts.

## Weaknesses — areas DBR does not do well

- **Cold start is slow.** Standard DBR ~1–3 min, ML ~3–5 min, GPU longer. Use pools or serverless.
- **Library install adds minutes.** Long requirements files compound cold-start pain.
- **Cross-version code portability is mediocre.** Spark + Delta behaviors evolve; LTS bumps require regression testing.
- **No first-class `requirements.txt` model.** You configure libraries via UI/API or `%pip` in notebooks. Reproducible builds need DCS or DABs.
- **GPU drivers/libraries are pinned per DBR.** Upgrading CUDA means upgrading DBR.
- **Serverless restricts what classic DBR allowed.** Init scripts, certain Python packages, certain Spark configs are off-limits. Forces refactor.
- **Single Python version per DBR.** No Python 3.10 + 3.12 side-by-side.
- **Memory tuning is on you.** Workers don't auto-tune executor memory; OOMs are common with naive defaults.
- **Logs scattered.** Driver log, executor log, init-script log, init script output — finding the right log is annoying.
- **DCS / custom images add CI burden.** You're maintaining base images.
- **R / Scala receive less attention than Python.** Some new features land in Python first.
- **Some open-source Spark features lag.** Databricks adds Photon + Delta + LDP features that aren't in OSS Spark; some OSS-only features lag behind in DBR.

---

## Sources

- [Databricks Runtime release notes](https://docs.databricks.com/aws/en/release-notes/runtime/)
- [DBR LTS support policy](https://docs.databricks.com/aws/en/release-notes/runtime/databricks-runtime-ver)
- [DBR 18.0 release notes](https://docs.databricks.com/aws/en/release-notes/runtime/18.0)
- [Databricks Container Services](https://docs.databricks.com/aws/en/clusters/custom-containers)
- [Init scripts](https://docs.databricks.com/aws/en/init-scripts/)
- [Cluster libraries](https://docs.databricks.com/aws/en/libraries/)
- [Glossary](../Glossary/glossary.md)
