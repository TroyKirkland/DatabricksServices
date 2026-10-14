# Mosaic AI Model Serving

## Level 100 — What it is

Mosaic AI Model Serving is **serverless model inference** on Databricks. You register an MLflow model (or wrap an external one), create a serving endpoint, and get a scalable REST API — with scale-to-zero, GPU options, payload logging, and Gateway integration. No clusters, no Kubernetes.

- **Serverless**: scale-to-zero by default; pay per request + provisioned capacity.
- **Open**: serves MLflow models (any framework via pyfunc), external models (OpenAI/Anthropic/etc.), and Foundation Model APIs.
- **Production-grade**: auto-scaling, payload tables, traffic splitting, Gateway policies.

## Level 200 — The product surface

- **Endpoint** — the addressable thing: `<workspace>/serving-endpoints/<name>/invocations`. Hosts one or more **served entities** (model versions or external models).
- **Served entity types**:
  - **Custom model** — a registered model in UC (any MLflow pyfunc).
  - **Agent endpoint** — an AI agent packaged as a custom model (supported as of 2026); same pyfunc surface, designed for multi-step agentic workloads.
  - **Foundation Model API (FMAPI)** — Databricks-hosted models (Llama, OpenAI GPT, Claude, Gemini, etc.).
  - **External model** — BYOK proxy to OpenAI / Anthropic / Bedrock / Cohere / etc. using your own credentials.
- **Traffic split** — route N% to entity A, M% to entity B (A/B, canary).
- **Compute spec** — workload type (CPU / GPU_SMALL / GPU_MEDIUM / GPU_LARGE), workload size, scale-to-zero on/off.
- **Auth**: workspace OAuth (U2M/M2M) for callers; the endpoint itself can call out using service credentials.
- **Payload table** — auto-created Delta table logging request/response for monitoring + offline eval.
- **Inference tables** — alias of payload table; UC-governed.
- **Routes through Unity Gateway** (optional; formerly Mosaic AI Gateway): rate limits, PII detection, fallback routing, smart routing.
- **Batch inference** — `ai_query()` in DBSQL or `mlflow.deployments.predict()` from notebooks.

## Level 300 — Architecture

```
[Caller (app / DBSQL / notebook)]
        │
        REST (Bearer token, JSON)
        │
        ▼
[Mosaic AI Model Serving endpoint]
        ├─ Routing (traffic split, fallback, retry)
        ├─ Authentication (workspace OAuth)
        ├─ Unity Gateway (optional)
        ├─ Inference container (model loaded; CPU or GPU)
        └─ Payload tap → Delta payload table (UC)
        │
        ▼
[Model response]
```

- **Inference containers** are managed by Databricks; you don't see the K8s/VM layer.
- **Cold start** when scaled to zero: ~1–60 s depending on model size and GPU type. Provisioned-capacity endpoints avoid this.
- **Concurrency**: per-endpoint autoscale based on request rate; configurable `min/max provisioned concurrency`.
- **Payload table** lands every request/response in UC for audit + drift + eval.

## Level 400 — Deep internals

### Endpoint lifecycle

- States: pending → ready → updating → ready → not-ready (on failure).
- Updates (new model version, scaling, traffic) are blue/green: new replicas spin up, traffic shifts, old replicas drain.
- Failures during update auto-rollback if the new version doesn't pass health checks.

### Compute selection

- **CPU**: workload sizes (Small, Medium, Large) → fixed CPU/RAM per replica.
- **GPU**: GPU_SMALL (e.g., 1×T4), GPU_MEDIUM, GPU_LARGE; one GPU class per endpoint replica.
- **Scale-to-zero**: replicas terminate after idle window; next call pays cold start.
- **Provisioned throughput** for FMAPI: dedicated tokens-per-second capacity, no cold start, predictable cost.

### Routing + traffic split

- Endpoint can host multiple served entities; each has a percentage.
- Used for canaries: 95% prod / 5% candidate.
- Fallback routing (with Gateway): if primary endpoint errors, fall back to secondary.

### Payload tables (inference tables)

- Auto-provisioned Delta table per endpoint when enabled.
- Schema includes: timestamp, request, response, latency, status, model version, user identity.
- Sampling configurable to avoid blowing storage costs.
- Used by MLflow eval, monitoring (drift), agent eval pipelines.

### Auth + identity

- Callers authenticate with workspace OAuth (M2M for service-to-service, U2M for interactive).
- Endpoint can be granted MANAGE / QUERY perms via UC.
- Endpoint identity: can call other Databricks resources (UC tables, Vector Search, Lakebase) using configured service credentials; this is how RAG agents talk to UC data.

### Unity Gateway integration

- If Unity Gateway is enabled, every request flows through it for rate limits, PII detection/redaction, payload logging, fallback routing, and smart routing.
- Can sit in front of internal endpoints, external models, and AI agents.

### Performance characteristics

- **Cold start**: small CPU model ~1–5 s; GPU model 10–60 s; large LLM 30 s–2 min.
- **Latency floor**: a few hundred ms for small models; LLMs depend on output token count.
- **Throughput**: replicas scale horizontally; per-replica concurrency tunable.
- **Streaming responses** (SSE) supported for LLM endpoints.

### Batch inference

- `ai_query(endpoint, prompt_col)` in DBSQL parallelises calls into the endpoint up to its concurrency.
- For huge batches, dedicated batch jobs via Spark + `mlflow.deployments.predict()` give more control.

### Limits / realities

- Per-replica memory caps; very large models need GPU_LARGE.
- Cold-start unpredictability without provisioned capacity.
- Payload tables can fill storage fast — sample aggressively.
- Concurrency limits per endpoint are workspace-level; raise via support.
- External model endpoints inherit upstream provider's rate limits.
- Endpoint logs are in cluster-style logs; payload table is the queryable history.
- Platform-level benchmark: Model Serving can handle over 25,000 queries per second with an overhead latency under 50 ms at the proxy layer (model inference time adds to this).

### Mental model

Mosaic AI Model Serving is **"a serverless API gateway in front of a model"**. Custom models (sklearn / pytorch / transformers / agents / pyfunc) and hosted/external models look the same to callers. The interesting design choices are: scale-to-zero vs provisioned, payload logging cadence, and Gateway-or-not.

---

## Anti-patterns — where you should not use Model Serving

- **Sub-10ms hard-latency requirements.** Even warm endpoints have ~50-200ms floor. Use in-process inference for that.
- **Heavy batch inference billions-of-rows.** A serving endpoint is one-call-per-row. Use Spark batch with model loaded in-process for huge batches.
- **Models that need GPU but call paths assume CPU.** Pay-per-second GPU cost is real.
- **Streaming inference at very high rates without provisioned.** Cold-start + scale-up lag costs latency.
- **Stateful models (long-lived sessions, server-side conversation memory).** Endpoints are stateless across calls; store state in Lakebase / UC.
- **Serving a model directly from an MLflow run URI.** Register to UC first; aliases give you promotion.
- **Per-customer custom endpoints at scale.** One endpoint per N customers is fine; thousands isn't.
- **Custom routing logic in front of one endpoint.** Use traffic split + Gateway instead of building your own router.
- **Endpoints as a long-term experiment runner.** Use jobs/notebooks; endpoints are for production serving.
- **Embedding sensitive prompts in payload tables without redaction.** Use Gateway PII features or redact upstream.

## Weaknesses — areas Model Serving does not do well

- **Cold start without provisioned capacity.** Scale-to-zero saves money but the first call hurts.
- **Per-endpoint concurrency caps.** Need to scale out endpoints or raise limits via support.
- **GPU scarcity.** Larger GPU classes can be backordered in some regions.
- **Payload table cost can balloon.** Sample carefully.
- **Limited custom container support.** Pyfunc is the path; full custom Dockerfiles aren't supported.
- **Streaming protocol details.** SSE works but timeouts at intermediaries (proxies, gateways) can bite.
- **Per-call observability less granular than self-hosted.** You get payload + latency; deeper introspection needs MLflow tracing inside the model code.
- **Per-region availability.** Some workspaces don't have GPU SKUs available.
- **Network egress.** External model calls pay egress from Databricks region.
- **Init / startup hooks are limited.** Heavy model-load code = long cold start; minimal opportunity to warm.
- **External model integrations vary in fidelity.** OpenAI/Anthropic features (function calling, tool use) work; bleeding-edge features lag.
- **Versioning UX in the UI is OK, not great.** API or DABs give better control.

---

## Sources

- [Mosaic AI Model Serving docs](https://docs.databricks.com/aws/en/machine-learning/model-serving/)
- [Provisioned throughput](https://docs.databricks.com/aws/en/machine-learning/model-serving/provisioned-throughput)
- [Payload tables / inference tables](https://docs.databricks.com/aws/en/machine-learning/model-serving/inference-tables)
- [External models](https://docs.databricks.com/aws/en/machine-learning/model-serving/external-models)
- [Glossary](../Glossary/glossary.md)
