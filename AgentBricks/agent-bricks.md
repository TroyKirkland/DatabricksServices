# Agent Bricks

## Level 100 — What it is

Agent Bricks is Databricks' **eval-driven agent builder**. Pick a use-case template (Knowledge Assistant, Genie Agent, Multi-Agent Supervisor), point it at your data, and Databricks generates a baseline agent + evaluation harness. You iterate on the agent by running evals, labelling traces, and tuning — all inside the lakehouse.

- **Use-case templates**: not "build any agent"; targeted patterns Databricks has tuned.
- **Eval-first**: every agent comes with a starter eval set; you grow it.
- **UC + MLflow + Model Serving + Vector Search wired together.**

## Level 200 — The product surface

- **Knowledge Assistant (KA)** — RAG-style Q&A agent over UC documents. Data: UC tables / UC volumes (PDFs, docs) or AI Search indexes. Uses an Instructed Retriever approach for improved accuracy. Builds an indexing pipeline + retrieval + answer-generation chain with citation tracking.
- **Genie Agent** — see the AI/BI Genie product page. Agent Bricks treats a Genie Space as a production-grade NL→SQL agent component you can curate, version, and orchestrate under a Supervisor.
- **Multi-Agent Supervisor (MAS)** — orchestrate multiple sub-agents (KA + Genie Agents + agent endpoints + Unity Catalog functions + MCP servers + custom agents) under a supervisor that routes questions to the right child.
- **Agent Bricks CLI** (Beta) — code-first alternative; scaffold, run, and deploy custom agents from the command line with managed memory, sessions, tools, and MLflow tracing wired up.
- **Evaluation harness** — built-in eval datasets + scorers; click "evaluate" to run, see traces, label.
- **Deploy** — register the agent as a Mosaic AI Model Serving endpoint with one click; supports streaming.
- **Optimization loop** — Databricks suggests prompt / instruction / retrieval tweaks based on eval results.
- **UC integration** — agents are UC objects; the underlying registered model, vector index, Genie space, payload tables are all UC-tracked.

## Level 300 — Architecture

```
[Source data: UC tables / UC volumes / documents]
        │
        ▼
[Agent Bricks pipeline]
    ├─ Knowledge Assistant: chunk → embed → Vector Search index → retrieval → LLM
    ├─ Genie Space: schemas + instructions + examples → NL→SQL → warehouse
    └─ Multi-Agent Supervisor: router LLM → child agents (KA / Genie / tools)
        │
        ▼
[Registered model in UC + Model Serving endpoint]
        │
        ▼
[Callers: app / DBSQL ai_query / external]
        │
        ▼
[MLflow traces + payload tables + eval harness]
        │
        └── iterate prompts / instructions / retrieval based on labelled failures
```

- **All components are managed by Databricks** but visible: the Vector Search index, the Model Serving endpoint, the MLflow run.
- **Evaluation is in-the-loop**: Agent Bricks treats eval datasets and labelled failures as first-class.

## Level 400 — Deep internals

### Knowledge Assistant pipeline

- Ingest: source UC tables/volumes are chunked (configurable splitter).
- Embed: uses a configured embedding model (default Databricks-hosted, swappable).
- Index: Delta Sync Vector Search index, governed by UC.
- Retrieval: hybrid (vector + keyword); top-k tunable.
- Generation: LLM call (default FMAPI, swappable to external via Model Serving + Gateway).
- Each step is tunable; Agent Bricks proposes defaults.

### Genie Space integration

- A Genie Space is the SQL-querying child; Agent Bricks exposes the Genie Conversation API as a tool callable by the supervisor.
- All Genie governance/RLS applies.

### Multi-Agent Supervisor

- A router LLM decides which child agent handles each user turn.
- Child agents can be: KA, Genie Agents, agent endpoints, Unity Catalog functions, MCP servers, sub-MAS, or custom agents.
- MCP (Model Context Protocol) servers standardise how agents connect to data and tools with a secure, consistent interface — any MCP-compatible server can plug in as a child.
- Supervisor maintains conversation state; children are stateless w.r.t. each other.
- Tool definitions declared as functions; supervisor uses function-calling-style structured output.

### Evaluation harness

- Starter dataset auto-generated from sample queries; you expand it.
- Scorers: built-in (Correctness, RetrievalGroundedness, Safety, Guidelines) + custom MLflow `@scorer` functions.
- Eval runs are MLflow runs; results comparable across iterations.
- Production traces sampled into UC; click "label" to add to eval dataset.

### Optimization loop

- Databricks computes failure patterns from eval scores.
- Suggests changes: prompt rewrites, chunk size adjustments, retrieval `k` increases, additional examples.
- You accept/reject; suggestions are tested against the eval dataset before applying.

### Deployment

- One-click deploy to Mosaic AI Model Serving with `ChatAgent` or `ResponsesAgent` schema.
- Endpoint supports streaming (SSE) for interactive UX.
- Payload table captures live traffic.
- Traffic-split between agent versions for canary.

### Auth and identity

- Agent endpoint identity calls UC tables, Vector Search, Genie, Model Serving with service credentials.
- Callers authenticate to the endpoint with workspace OAuth.

### Agent Bricks CLI (Beta)

- `databricks agent bricks` CLI commands scaffold a project, define agent logic in code, and deploy to a Model Serving endpoint.
- Managed memory and session handling are wired in (no manual state management boilerplate).
- Tools (UC functions, MCP servers, custom functions) declared in config; CLI injects them at deploy time.
- MLflow tracing auto-wired; traces appear in the experiment immediately.
- Beta: expect breaking changes; not yet recommended for production-critical agents.
- When you need flexibility beyond the GUI templates, CLI is the upgrade path before going full LangGraph.

### Limits / realities

- Agent Bricks is **opinionated**; deviating heavily means dropping into MLflow + LangChain/LangGraph manually.
- The "agent" is materially a curated chain of Databricks primitives; if you need a custom orchestration framework, build it yourself.
- Eval scorers can be expensive (LLM-as-judge) — sample, don't run full eval every iteration.
- Multi-agent fan-out costs add up; each call is an LLM call.

### Mental model

Agent Bricks is **"templates + eval loop + Databricks primitives wired together"**. The product value is not in raw flexibility (custom code beats it there) but in opinionated patterns + the eval feedback loop that makes iterating quality tractable.

---

## Anti-patterns — where you should not use Agent Bricks

- **Bespoke multi-step agents.** Agent Bricks is templated; for custom orchestration use LangGraph/LangChain on Databricks directly.
- **Edge / low-latency inference.** Agents are multi-call by nature; latency is in seconds.
- **Use cases that don't fit the templates.** Knowledge Assistant for non-text data, MAS for trivial linear flows.
- **Code generation agents.** Templates are tuned for Q&A / SQL / orchestration; specialised codegen needs custom.
- **High-volume customer-facing API.** Per-call cost (LLM + retrieval + maybe SQL) makes consumer-scale uneconomic vs cheaper specialised models.
- **Domains where eval is unreliable.** If you can't define correctness, you can't tune; Agent Bricks won't fix that.
- **No-curation Genie spaces.** Linking a half-baked Genie space into MAS makes everything worse.
- **Real-time decisions (under 500ms).** Multi-step chains can't.
- **Replacing a human in a high-stakes decision without explicit human-in-the-loop.** Agent quality is a function of eval; eval is sampling.
- **One-time data extraction.** Use a notebook + a focused LLM call.

## Weaknesses — areas Agent Bricks does not do well

- **Opinionated templates.** Customisation has a ceiling; complex agents must drop to code.
- **Eval cost.** LLM-judge scorers per row × frequent eval = real spend.
- **Fan-out cost in MAS.** Every routing decision is an LLM call.
- **Multi-turn state handling.** Conversation memory is implicit; complex stateful flows need explicit design.
- **Observability is via MLflow traces.** Cross-trace pattern analysis isn't built-in.
- **Limited tool ecosystem.** Function-calling tools work but aren't as rich as full LangGraph.
- **Suggestions aren't always great.** Optimization loop offers ideas; quality depends on eval coverage.
- **Mixed model providers add complexity.** External + internal models in one MAS = more failure modes.
- **Cross-agent context window pressure.** Long histories balloon token costs.
- **Heavy reliance on Vector Search for KA.** Retrieval quality caps overall agent quality.
- **Genie integration inherits Genie limits.** Curation rot in Genie hurts MAS.
- **Versioning UX is OK, not great.** Agent versioning + endpoint versioning + eval dataset versioning don't compose cleanly yet.

---

## Sources

- [Agent Bricks docs](https://docs.databricks.com/aws/en/generative-ai/agent-bricks)
- [Knowledge Assistant](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/knowledge-assistant)
- [Supervisor Agent (Multi-Agent)](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/supervisor-agent)
- [MLflow GenAI eval](https://mlflow.org/docs/latest/llms/llm-evaluate/index.html)
- [Glossary](../Glossary/glossary.md)
