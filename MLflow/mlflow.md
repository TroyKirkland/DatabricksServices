# MLflow on Databricks

## Level 100 — What it is

MLflow is the **open-source standard** for ML experiment tracking, model registry, agent tracing, and evaluation. On Databricks, it's preinstalled in every DBR, registered models live in Unity Catalog, and tracing is wired into the platform's compute and agent runtimes.

- **Tracking**: log runs (params, metrics, artifacts) under experiments.
- **Registry**: registered models in UC; versions, stages, lineage.
- **Tracing**: span-based observability for LLM/agent applications.
- **Evaluation**: `mlflow.genai.evaluate()` for agent quality, plus traditional ML eval.

## Level 200 — The product surface

- **Experiments + Runs** — runs are parented under experiments; each run logs params, metrics, tags, artifacts (any file), and (in MLflow 3) traces.
- **Models** — `mlflow.pyfunc` flavors for portability; framework flavors (sklearn, pytorch, tensorflow, transformers, langchain, etc.); custom code via `python_function`.
- **Registered models** — UC models with versions; promoted via aliases (`@production`, `@staging`).
- **Tracing API** — `@mlflow.trace`, `mlflow.langchain.autolog()`, `mlflow.openai.autolog()`, etc.
- **GenAI evaluation** — `mlflow.genai.evaluate()` with scorers: built-ins (`Guidelines`, `Correctness`, `RetrievalGroundedness`, `Safety`) and custom `@scorer` functions.
- **Logged Models** (MLflow 3) — persistent model objects that track progress across the full lifecycle; linked to runs, metrics, params, and generation code; a separate concept from a UC-registered model version.
- **Deployment Jobs** (MLflow 3) — managed workflow that sequences evaluate → approve → deploy for a model version; UC-governed with activity logging.
- **Prompt management** — version, manage, and iterate on prompt templates used across AI applications; storable and comparable across agent versions.
- **Deployment integrations** — register → deploy as Mosaic AI Model Serving endpoint.
- **Auto-logging** — most frameworks have one-line autolog that captures params/metrics/model.
- **MLflow SDK** in Python, TypeScript/JavaScript, R, Java, and REST.

## Level 300 — Architecture

```
[Training code / agent runtime]
        │
        ▼
   mlflow.* SDK ── HTTPS ──► [Databricks MLflow tracking server]
                                 │
                                 ├─ Experiments / Runs metadata in workspace
                                 ├─ Artifacts in UC-managed cloud storage
                                 └─ Traces in workspace traces store

[Registered models live in UC: catalog.schema.model_name + versions]
        │
        └── consumed by Mosaic AI Model Serving / Spark batch / external clients
```

- **Tracking server**: workspace-attached; experiments are workspace-local but registered models live at UC scope.
- **Artifacts**: stored in cloud object storage; UC manages credentials.
- **Traces**: separate store optimised for span-based queries; surfaced in MLflow UI under each experiment.
- **Lineage**: training data tables logged on runs propagate through to UC lineage when sourced from UC tables.

## Level 400 — Deep internals

### Run lifecycle

- `mlflow.start_run(...)` creates a run, returns context; logging APIs target the active run.
- Nested runs: `nested=True` for hyperparameter sweeps; parent-child relationships preserved.
- On end (or context exit), the run is sealed and immutable except for tags/description.

### Models and pyfunc

- Models saved with `mlflow.<framework>.log_model(...)` produce a directory with: `MLmodel` metadata, code, weights/serialisation, and a `python_function` wrapper.
- pyfunc is the universal interface: anything (sklearn, pytorch, an agent chain, a custom class) can be a pyfunc.
- For agents specifically: `ChatAgent` and `ResponsesAgent` standardise the interface (input/output schemas) for chat-style deployments.

### Registered models in UC

- `mlflow.register_model("runs:/<run-id>/model", "catalog.schema.name")` creates a new model version.
- Aliases (`@production`) are pointers to versions; promote by updating the alias.
- Stages (`Production`/`Staging`/`Archived`) exist legacy; aliases are the modern way.
- UC governance: GRANT EXECUTE on models; lineage; ABAC.

### Tracing

- A trace is a tree of spans (functions called, latency, inputs, outputs).
- Auto-logging instruments common frameworks: LangChain, LangGraph, OpenAI, DSPy, CrewAI, AutoGen.
- Manual instrumentation: `@mlflow.trace` decorator or `mlflow.start_span(...)`.
- Traces are queryable by experiment, time, tags; can be filtered by status, attributes, latency.
- Trace ingestion supports custom span attributes (token counts, retrieval hits).

### Evaluation

- `mlflow.genai.evaluate(data, predict_fn, scorers=[...])` runs predict over a dataset, scores with provided scorers, logs results as a run.
- Built-in scorers wrap LLM judges (Guidelines, Correctness, Safety) or deterministic checks (RetrievalGroundedness, ExactMatch).
- Custom `@scorer` decorators define your own; receive (input, output, expected) and return numeric or boolean.
- Eval result tables are logged to UC; comparable across runs.

### Production loop

- Production traces (from a deployed model) can be sampled into a UC table; you label them, build an eval dataset, run `mlflow.genai.evaluate` against it, iterate the prompt/agent.

### Logged Models and Deployment Jobs (MLflow 3)

- **Logged Models** are first-class MLflow 3 objects, separate from UC registered model versions. A Logged Model persists as its own entity across the development arc: created during a run, linked to metrics/params/code, and later promotable to a UC registered version. This replaces the friction of tracking "which run ID held the best artifact."
- **Deployment Jobs** are managed pipeline objects: define a sequence of evaluate → approve → deploy stages. Each stage is UC-governed; transitions are activity-logged. Reduces the manual alias-promotion ceremony for model governance.
- Both concepts complement the existing run/registered-model primitives; they don't replace them.

### Prompt management

- Store prompt templates in MLflow; assign versions and aliases.
- Compare prompt versions against an eval dataset to quantify regression before deploying a new template.
- Surfaced in the MLflow UI; accessible via SDK.
- Particularly useful in Agent Bricks / LLM app iteration cycles.

### Auto-logging gotchas

- Auto-logging hooks into framework internals; some operations skip (e.g., custom training loops).
- For PyTorch Lightning / Hugging Face Trainer, you log via the framework callbacks rather than `mlflow.<x>.autolog()` for full control.

### Limits / realities

- Experiment search returns up to a paginated limit; for large search use the search API with cursors.
- Artifact storage is in your cloud account (UC volumes / external location); cleanup is on you for old runs.
- Trace storage scales but has cost; production traces should be sampled, not all-on.
- Auto-log lock-step with framework versions; mismatched versions = silent gaps.
- MLflow 3 introduced new tracing data model; some MLflow 2.x patterns are deprecated but supported.

### Mental model

MLflow on Databricks is **the connective tissue between training and serving**, plus the **observability layer for agents**. The biggest shift in MLflow 3 era: agent tracing + evaluation are first-class, not bolted-on, and UC owns model identity.

---

## Anti-patterns — where you should not use MLflow

- **Logging huge artifacts on every run.** 1 GB datasets logged 100 times per day = expensive UC storage churn. Log references, not copies.
- **Sub-second model serving from MLflow directly.** Use Mosaic AI Model Serving for production endpoints; MLflow handles registration, not serving.
- **Streaming production traces with no sampling.** Quickly fills trace storage; sample by user/request type.
- **Hyperparameter sweeps that don't nest.** Use parent/child run hierarchy; flat 10K sibling runs is unmanageable.
- **Registering every dev model in production catalog.** Use a `dev` catalog; promote to `prod` deliberately.
- **Custom pyfunc when a framework flavor exists.** sklearn, transformers, langchain flavors have battle-tested logging; reach for pyfunc only when needed.
- **Manual tracing of frameworks that have autolog.** Use autolog and add manual spans for your own code.
- **Treating experiments as production audit.** Use UC system tables (`system.access.audit`) for compliance.
- **Storing PII in logged params.** Logged params are searchable; redact first.
- **Local file artifacts referenced by relative paths.** They die when the run ends. Use UC volumes.

## Weaknesses — areas MLflow on Databricks does not do well

- **Search across many experiments is slow.** Use focused experiments and pagination.
- **Trace storage cost at scale.** Sampling and TTL are mandatory for prod.
- **Auto-log version pinning.** Framework upgrades occasionally break autolog silently.
- **Model registry alias UX is OK, not great.** Some teams prefer their own alias conventions; the built-ins are limited.
- **Evaluation scorers are LLM-judge cost-heavy.** Each eval row = an LLM call. Budget accordingly.
- **Cross-experiment comparisons are manual.** No first-class "compare these two eval runs across experiments".
- **Distributed training instrumentation is uneven.** Some frameworks log only on the driver.
- **Notebook UX for traces.** Display is OK but deep navigation is awkward; UI is better.
- **Limited custom UI on the trace viewer.** You see what MLflow shows.
- **Versioning of eval scorers themselves.** No first-class registry; bake them into a wheel.
- **Multi-region MLflow workspaces.** Models live per metastore region.
- **Trace-to-prod tracing handoff is manual.** Wiring sample → eval dataset → re-train is on you.

---

## Sources

- [MLflow docs](https://mlflow.org/docs/latest/index.html)
- [MLflow on Databricks](https://docs.databricks.com/aws/en/mlflow/)
- [GenAI evaluation](https://mlflow.org/docs/latest/llms/llm-evaluate/index.html)
- [Tracing](https://mlflow.org/docs/latest/llms/tracing/index.html)
- [Models in UC](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/)
- [Logged Models and Deployment Jobs (MLflow 3)](https://docs.databricks.com/aws/en/mlflow/)
- [Glossary](../Glossary/glossary.md)
