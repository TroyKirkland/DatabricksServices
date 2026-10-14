# Unity Gateway (formerly Mosaic AI Gateway)

## Level 100 — What it is

Unity Gateway (previously called Mosaic AI Gateway; renamed as of late 2026) is a **policy and observability layer** in front of model endpoints, AI agents, and MCP servers. It centralises rate limits, PII detection, payload logging, fallback routing, smart routing, and cost tracking — so apps call one governed endpoint regardless of the underlying model or agent.

- **One pane** for many models and agents (Databricks FMAPI — GPT, Claude, Gemini, Llama, and more — plus external BYOK endpoints, custom models, and AI agents).
- **Governance**: rate limits per identity, PII redaction, audit, payload tables; extends to runtime interactions between models, agents, MCP servers, and tools.
- **Resilience**: fallback routing, retry, traffic split, smart routing.
- **Smart routing**: automatic model selection for certain task types (e.g., coding queries) to optimise cost without sacrificing quality.

## Level 200 — The product surface

- **Gateway-enabled endpoint** — a Model Serving endpoint with Unity Gateway features turned on. Per-endpoint or workspace-wide.
- **Rate limits** — by identity, by endpoint, by token usage. Configurable per role.
- **Budget management** — caps on spend per endpoint or principal alongside rate limits.
- **PII detection** — built-in detector with categories (PHONE, EMAIL, SSN, etc.); modes: detect, redact, block.
- **Guardrails** — service policies (built-in and custom) controlling how each request and response proceeds based on content and caller identity: prompt-injection, toxicity, block-lists.
- **Fallback routing** — if primary endpoint fails (rate-limited, errored, slow), route to a secondary served entity.
- **Smart routing** — automatic model selection for task types (e.g., coding) to minimise cost without quality regression.
- **Payload logging** — request/response written to a Delta payload table; usage/token columns derived.
- **Usage tracking** — token counts and cost rolled up in `system.serving.*` system tables.
- **Configuration**: UI + REST + DABs.
- **Scope extended to agents and MCP**: Unity Catalog registration of AI assets (agents, MCP servers, tools) with GRANT-based access control and runtime governance.

## Level 300 — Architecture

```
[Caller] ──► [Gateway-enabled endpoint]
                │
                ▼
        ┌──────────────────────────┐
        │ Gateway request pipeline │
        │ ├ AuthZ check            │
        │ ├ Rate-limit check       │
        │ ├ PII detect/redact      │
        │ ├ Guardrail (input)      │
        │ └ Route to served entity │
        └──────────────────────────┘
                │
                ▼
        [Model: FMAPI / external / custom]
                │
                ▼
        ┌──────────────────────────┐
        │ Gateway response pipeline│
        │ ├ Guardrail (output)     │
        │ ├ PII scrub (optional)   │
        │ ├ Payload log to UC      │
        │ └ Usage emit             │
        └──────────────────────────┘
                │
                ▼
            [Caller]
```

- **All requests flow through Gateway** when enabled, including streaming responses.
- **Fallback routes** are evaluated on error or timeout; multi-hop fallbacks possible (primary → secondary → tertiary).
- **Payload table** is a UC Delta table populated asynchronously to avoid blocking.

## Level 400 — Deep internals

### Rate limits

- Token-based + request-based.
- Scope: per principal (user/SP), per endpoint, per route.
- Backoff with `Retry-After` headers when exceeded.
- Window: sliding (typical) — depends on configuration.

### PII detection

- Built on Databricks' PII classifier; categories tagged in payload-table columns.
- Modes:
  - **Detect** — annotate, don't change.
  - **Redact** — replace with `<PHONE>` etc. in both request (to model) and payload table.
  - **Block** — reject request with explicit error.
- False positives/negatives exist; treat as a defense layer, not a guarantee.

### Guardrails

- Built-in: prompt-injection detection, toxicity, profanity, custom block-lists.
- Custom: regex / SQL UDF on payload columns.
- Applied at input (before model call) and/or output (before returning to caller).

### Fallback routing

- Configured per endpoint: ordered list of served entities.
- Triggers: HTTP 5xx, rate-limit, timeout > threshold.
- Streaming responses: fallback only at session start, not mid-stream.
- Audit logs every fallback event.

### Payload tables

- UC Delta table per Gateway endpoint.
- Schema: request, response, latency, status, identity, token counts, PII categories, fallback applied.
- Sampling configurable; default is full logging (warn: storage growth).

### Usage and cost

- `system.serving.endpoint_usage` (and similar) aggregate per-endpoint token usage.
- Joinable with `system.billing.usage` for $ per token consumed.
- Cost attribution per principal possible via payload table identity column.

### External model semantics

- Gateway holds credentials for external providers (in UC).
- Requests transparently re-shape provider-specific schemas (OpenAI, Anthropic) into a unified format if the served entity defines such mapping.
- Egress through Gateway means an extra hop; latency cost is small but non-zero.

### Streaming behaviour

- Server-Sent Events supported; Gateway pipelines work on the request and at-end-of-stream for token counting + final guardrail check.
- Mid-stream output guardrails are limited; for hard requirements, scan post-hoc.

### Multi-endpoint / shared Gateway

- A workspace can configure Gateway policies that apply across endpoints (org-wide defaults), with per-endpoint overrides.
- Useful for "no PII leaving the platform" policies enforced uniformly.

### Limits / realities

- Rate-limit enforcement isn't strict-real-time — small over-shoot possible under burst.
- PII detection has false-positive cost (over-redaction) and false-negative risk (real leak).
- Payload table size: scale carefully; sample for high-volume endpoints.
- Adding Gateway to an existing endpoint can change response latency profile slightly.
- External providers' own rate limits still apply *under* Gateway; Gateway can't conjure capacity.

### Smart routing

- Unity Gateway can automatically route certain request types (e.g., coding tasks) to a cheaper or more efficient model without the caller specifying.
- Optimises spend while maintaining quality targets; configurable per endpoint.
- Selection logic is Databricks-managed; not a general arbitrary routing DSL.

### Mental model

Unity Gateway is **"a smart proxy for model endpoints, agents, and MCP tools"**. Same role Apigee/Cloudflare play for web APIs — auth, throttling, observability, content policies — but tuned for LLM specifics (tokens, PII, prompt injection) and extended to the full agentic runtime.

---

## Anti-patterns — where you should not use Unity Gateway

- **Trivial single-internal-model deployments.** If you serve one model, internally, with no compliance requirements, the overhead may not pay back.
- **Trust PII detection as the only line of defense.** It catches a lot; it doesn't catch everything.
- **Use Gateway as a custom LLM router for very complex routing logic.** Built-in fallback works; arbitrary "if-then-else" routing isn't first-class.
- **Mid-stream content moderation expectations.** SSE limits this; scan transcripts post-hoc.
- **As a substitute for external provider's own quotas.** Provider limits sit underneath Gateway.
- **For inter-service / microservice routing (non-LLM).** Use a normal API gateway / service mesh.
- **High-throughput edge inference.** Each request pays the proxy hop.
- **Without payload table cost planning.** Volume can blow storage budgets fast.
- **Multi-org collaboration.** Use Clean Rooms; Gateway is for in-workspace policy.
- **As a one-stop "agent framework".** Gateway governs endpoints; agent orchestration is separate (Agent Bricks / LangGraph).

## Weaknesses — areas Unity Gateway does not do well

- **PII false positives/negatives.** No model is perfect; tune carefully.
- **Limited customisation of guardrails.** Built-ins are good, custom rules are basic.
- **Streaming guardrail blind spot.** Can't fully moderate mid-stream.
- **Per-endpoint or org-wide; less middle ground.** Policy hierarchies aren't very rich.
- **Rate-limit enforcement isn't strict-realtime.** Burst over-shoot possible.
- **Audit/observability is via UC tables, not real-time dashboards.** Build your own.
- **Cost attribution requires SQL.** No first-class "cost per user this week".
- **External provider header pass-through can be lossy.** Some provider-specific options don't traverse.
- **Latency overhead for very small calls.** Tens of ms can matter for high-volume cheap calls.
- **Failover-on-error has limits.** Mid-call failures during streaming are hard.
- **Multi-region setup limited.** Gateway lives per workspace/region.
- **Policy testing tooling is basic.** Hard to safely test new guardrails without affecting live.

---

## Sources

- [Unity Gateway docs](https://docs.databricks.com/en/ai-gateway/) — as of September 29, 2026 (formerly Mosaic AI Gateway)
- [Rate limits](https://docs.databricks.com/aws/en/ai-gateway/rate-limits)
- [PII detection](https://docs.databricks.com/aws/en/ai-gateway/pii-detection)
- [Fallback routing](https://docs.databricks.com/aws/en/ai-gateway/fallback)
- [Usage tracking](https://docs.databricks.com/aws/en/ai-gateway/usage)
- [Glossary](../Glossary/glossary.md)
