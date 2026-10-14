# Databricks Apps

## Level 100 — What it is

Databricks Apps are **hosted web applications** that run inside the Databricks workspace. Bring a Python or Node app (Streamlit, Dash, Flask, FastAPI, Gradio, Reflex, or React/Node), declare resources in `app.yaml`, deploy. The app gets workspace OAuth, access to UC tables / volumes / serving endpoints / Lakebase, and an HTTPS URL.

- **Hosted**: no separate cluster / VM / Kubernetes to manage.
- **UC-integrated**: app identity has fine-grained UC access.
- **Multi-framework**: Python web frameworks and Node apps supported.

## Level 200 — The product surface

- **`app.yaml`** — the manifest. Declares:
  - `command:` — how to run the app (e.g., `streamlit run app.py`).
  - `env:` — environment variables and references to resources.
  - `resources:` — UC tables, UC volumes, UC connections, serving endpoints, Lakebase instances, secrets, SQL warehouses, Genie Agents, MLflow experiments, AI Search indexes, user-defined functions, and other apps (app-to-app); each gets a config-injected URL or token.
  - `scopes:` — workspace OAuth scopes the app requests.
- **Supported runtimes**: Python (Streamlit, Dash, Gradio, Flask, FastAPI, Reflex), Node (React, Angular, Svelte, Express, FastAPI + React via APX).
- **App telemetry**: configure to collect traces, logs, and metrics and store them in Unity Catalog for governance and observability.
- **Auth model**:
  - **App identity** — service-principal-like; app code uses this for backend calls (UC, serving, Lakebase).
  - **On-behalf-of (OBO)** — for some calls, app can act with the end user's identity, enabling per-user RLS.
- **HTTPS endpoint** — `https://<app-name>-<hash>.cloud.databricks.com`. Workspace SSO required by default; can configure broader access via account-level features.
- **Logs and observability** — app stdout/stderr in workspace UI; integration with workspace logs.
- **Deployment** — CLI (`databricks apps deploy`), DABs, or UI.

## Level 300 — Architecture

```
[Browser]
   │ HTTPS, workspace OAuth (SSO)
   ▼
[App endpoint (Databricks-hosted)]
   │
   ▼
[App container running your framework]
   ├─ env vars injected from app.yaml resources
   │   (UC table URLs, Lakebase conn, serving endpoint URL, secrets)
   ├─ App identity calls UC / endpoints / Lakebase via databricks-sdk
   └─ Optional OBO → user identity for per-user RLS calls
        │
        ▼
[UC tables / Serving endpoints / Lakebase / Volumes]
```

- **Container runtime** is Databricks-managed; you don't pick the underlying image.
- **Resource binding**: declared resources are translated to env vars / connection objects available in code (e.g., `DATABRICKS_HOST`, `LAKEBASE_URL`).
- **Networking**: outbound to UC + serving + Lakebase native; arbitrary internet egress is constrained by account-level network policy.

## Level 400 — Deep internals

### App lifecycle

- Create: define `app.yaml` + code → `databricks apps deploy` packages and uploads.
- Start: Databricks pulls image, sets env from manifest, runs `command`.
- Update: re-deploy → new revision, traffic shifts.
- Stop / restart via UI / API.

### Resource resolution

- `resources:` entries are templated as env vars. Example: a serving-endpoint resource becomes `MY_ENDPOINT_URL`, `MY_ENDPOINT_NAME` etc.
- Secrets pulled from Databricks Secret Scopes and injected as env at startup.
- UC objects: `databricks-sdk` clients use injected `DATABRICKS_HOST` + token; tools like the SQL connector for Python or pyspark via Databricks Connect work natively.
- Apps run with least privilege — resources must already exist before being bound; the app identity gets the specific permissions of each declared resource.

### Authentication paths

- **App auth (workspace OAuth M2M)**: app identity is a service principal; gets an OAuth token at start (refreshable).
- **OBO (user) calls**: when app pages call back to Databricks REST APIs as the user, the framework provides a way to forward the user's bearer token; UC sees the user identity.
- **External APIs**: secrets in env; outbound HTTPS subject to network policy.

### Compute and scaling

- App runs as a managed container with allocated CPU/memory.
- Autoscaling for some configurations; predominantly single-instance with vertical sizing.
- Scale-to-zero supported; cold start a few seconds.
- Heavy-load apps benefit from higher tier; really heavy compute should run as a Job and the app should orchestrate / display results.

### Networking

- Same VPC/VNet as workspace (for relevant configs).
- Outbound to UC + serving + Lakebase native.
- Custom outbound: requires account network policy allowance.
- Inbound: HTTPS only, workspace-OAuth-protected by default.

### Logs and metrics

- stdout/stderr captured and shown in workspace UI.
- App metrics (request rate, latency) available in UI.
- **App telemetry**: declare a telemetry configuration in `app.yaml` to route traces, logs, and metrics to Unity Catalog tables — enables governance and cross-app querying.
- For additional observability, instrument the app yourself (Datadog, Prometheus push, MLflow tracing for AI calls).

### Integration patterns

- **AI assistant UI** on top of Agent Bricks / Model Serving.
- **Data entry forms** writing to Lakebase / UC.
- **Internal admin dashboard** for ops with embedded charts and triggers.
- **Custom data product portal** for external (account-level viewer-paid) audiences.
- **Embedded apps**: apps can be iframed into external websites, with workspace OAuth handling authentication in the embedding context.

### Limits / realities

- Resource sizing has caps; for very heavy apps, externalize compute (Jobs / Model Serving).
- Long-running websockets / streaming: supported but watch idle timeouts.
- Cold start: scale-to-zero saves money but adds first-request latency.
- Outbound egress limited by network policy.
- Apps that need very long-running tasks should hand off to Jobs and poll, not block the request thread.
- Identity-aware routing for multi-user state: stash state in Lakebase or UC, not in-process.

### Mental model

Databricks Apps are **"managed Heroku-like hosting for web apps, with first-class Databricks integration"**. Best when your app's core value is acting on lakehouse data and using AI endpoints; not the right venue for general-purpose B2C SaaS or anything that needs heavy custom infra.

---

## Anti-patterns — where you should not use Databricks Apps

- **General-purpose B2C SaaS.** Bring-your-own-infra is better there.
- **Heavy compute (training, video transcoding) inside the app.** Hand off to Jobs / Model Serving.
- **Apps with extensive custom infra (multiple back-ends, queues, message buses).** Use a real platform.
- **Hardcoded long-running blocking requests.** Request thread blocks the worker; use async patterns + Jobs.
- **In-process multi-user session state.** Container can restart; use Lakebase / UC for state.
- **External SaaS dashboards.** Use a normal hosting platform; Apps' value is the Databricks integration.
- **Apps that need outbound egress to many third parties without account-level network configuration.** Will fail closed.
- **Apps that fight workspace OAuth (custom auth flows).** Use the platform's auth.
- **Tens of thousands of concurrent users.** Scaling is limited; not a CDN-fronted SaaS host.
- **Without resource declaration discipline.** Hardcoding hosts/URLs in code instead of `app.yaml` resources causes drift.

## Weaknesses — areas Databricks Apps do not do well

- **Limited runtime customisation.** You pick a framework + manifest; no Dockerfile freedom.
- **Egress control is account-level.** Network policy changes need admin.
- **Scaling has limits.** Not built for SaaS-scale concurrent users.
- **Cold start.** Scale-to-zero saves money but hurts first-request latency.
- **Logs UI is OK, not great.** Self-instrument for deep observability.
- **Long-running websocket reliability.** Idle timeouts can disconnect.
- **OBO call ergonomics.** Forwarding user tokens through frameworks needs care.
- **Local dev mismatch.** Running locally vs in Apps reveals env differences.
- **Per-app secret management.** Secret scopes integrated, but rotation tooling basic.
- **UI for app versioning is limited.** Use DABs for proper CI/CD.
- **Cross-region failover not native.** App lives in one workspace/region.
- **Database connections need pooling discipline.** Lakebase connections from containers can leak under load if app code doesn't pool.

---

## Sources

- [Databricks Apps docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
- [App resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources)
- [Authentication / OBO](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
- [Glossary](../Glossary/glossary.md)
