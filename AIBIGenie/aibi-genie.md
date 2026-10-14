# AI/BI Genie

> **Rebrand (2026):** The product family is now **Genie** with three components: **Genie Agents** (formerly "Genie Spaces" — the configuration unit); **Genie One** (the end-user natural-language interface); **Genie Code** (AI coding assistant for developers — separate capability). This doc covers Genie Agents and Genie One. API paths still use `/genie/spaces/` as the resource name.

## Level 100 — What it is

AI/BI Genie is Databricks' **natural-language interface to SQL data**. Data teams configure **Genie Agents** (bounded sets of UC tables/views + instructions + example queries + metric views); end users interact via **Genie One** (browser or mobile) in plain English. Genie translates to SQL, executes on a SQL warehouse, and returns results + charts.

- **NL → SQL**, scoped to a curated agent.
- **Curated, not "ask anything"**: trust comes from constraints (the agent scope, instructions, examples, metric views).
- **Self-serve analytics** without users learning SQL.
- **Genie One pricing**: free through January 31, 2027. **Genie Code** (developer AI assistant) is pay-as-you-go from July 8, 2026 with a monthly per-user free allowance.

## Level 200 — The product surface

- **Genie Agent** (formerly Genie Space) — the unit of configuration. Contains: a name, a target SQL warehouse, a list of UC tables/views, instructions text, example SQL pairs ("question → query"), trusted assets, and UC [[glossary:Metric Views]].
- **Instructions** — free-text guidance: column definitions, business rules, naming conventions, things to avoid.
- **Example queries** — labelled NL/SQL pairs used as few-shot training for the model.
- **Trusted assets** — verified queries, certified by a steward, that Genie can reuse as building blocks.
- **Metric Views** — UC semantic-layer metrics that Genie Agents can reference; improves accuracy for pre-defined business KPIs.
- **Genie One** — the end-user conversational interface (browser and mobile — iOS/Android via Genie Mobile). Multi-turn; answers are charts, tables, or NL summaries.
- **Conversation API (Chat mode)** — `POST /api/2.0/genie/spaces/{space_id}/start-conversation`; subsequent `send-message` turns. Returns generated SQL, query ID, result preview, chart proposal. Paths still use `spaces` as the resource name.
- **Agent mode API** — programmatically send prompts and receive streamed reasoning, SQL results, and final reports with citations; newer addition alongside Chat mode.
- **Sharing** — agent permissions: CAN_VIEW (use), CAN_EDIT (curate), CAN_MANAGE.
- **Linked to dashboards** — a dashboard can launch a Genie Agent pre-loaded with its context.

## Level 300 — Architecture

```
Genie One (user) ──► [Genie LLM]
                          │  uses Agent context:
                          │   - tables/views + schemas
                          │   - instructions
                          │   - example queries
                          │   - trusted asset SQL
                          │   - metric view definitions
                          ▼
                     Generated SQL
                          │
                          ▼
               [SQL Warehouse executes]
                          │
                          ▼
               Result + chart proposal
                          │
                          ▼
          Returned to Genie One; conversation continues
```

- The LLM is a Databricks-hosted foundation model (subject to change; Databricks manages model upgrades).
- Generated SQL is **shown to the user** and editable; users can tweak before re-running.
- All SQL runs against the configured warehouse with the **user's identity**, so UC row/column policies apply.
- A "trust" score is shown per response: certified-query reuse → high; novel generation against unfamiliar table → lower.

## Level 400 — Deep internals

### Agent configuration as the trust boundary

- The agent's tables, views, and metric views are the entire universe Genie can query. Outside-agent objects don't exist as far as Genie is concerned.
- Instructions are injected into the prompt. **Effective instruction prose** matters: column units, gotchas, business definitions, JOIN keys.
- Example pairs serve as few-shot context. 5–20 well-chosen examples dramatically improve quality; more is not always better.
- Trusted assets (certified queries) get strong preference: Genie tries to reuse them or extend them rather than generate fresh SQL.
- Metric views give Genie pre-defined measure semantics; prefer them over raw fact tables for KPI questions.

### Generation pipeline

- Parse the question + conversation history.
- Resolve mentioned entities against space schemas + instructions.
- Plan SQL: prefer certified asset reuse → modify reusable asset → fresh generation.
- Validate: parse the SQL, dry-run against schema, fix obvious errors.
- Execute on the configured warehouse with user identity.
- Compose answer: chart suggestion + summary + raw rows.

### Execution semantics

- Each question = one SQL execution. Conversation history may inject filters into follow-ups ("now just for Q4") rather than re-asking the model from scratch.
- Result-cache hits if the SQL matches exactly.
- Warehouse identity = user's; UC enforces. If a user can't see a column, Genie's SQL fails closed, not silently.

### Trust signals

- Used a trusted asset = high confidence badge.
- Hit a column not in any example or instruction = lower confidence; Genie tells the user.
- Repeated failure / multiple attempted SQL drafts = visible to the user.
- All conversations land in a per-space history.

### Multi-turn handling

- Genie maintains conversation state per user per space.
- Follow-up resolution is heuristic: "this customer" / "filter to last week" usually works; "now compare X to Y over time" can require explicit re-anchoring.

### Conversation API (Chat mode and Agent mode)

- **Chat mode**: `POST /api/2.0/genie/spaces/{space_id}/start-conversation`; subsequent `POST .../send-message` adds turns. Returns: generated SQL, query ID, result preview, chart proposal. Embeddable in custom apps; auth follows workspace identity. API paths still use `spaces` as the resource name even after the Genie Agents rebrand.
- **Agent mode**: programmatically send prompts and receive streamed reasoning + SQL results, with final reports including citations. Newer capability designed for agentic applications.

### Curation lifecycle

- Stewards monitor "thumbs down" / unanswered / low-confidence questions → add examples or instructions → re-test.
- Trusted assets are promoted from past good queries.
- An agent is never "done" — quality is a function of ongoing curation.

### Limits / realities

- Space size matters: too many tables (>20–30) degrade quality. Split into focused spaces.
- Joins across many tables are hard; pre-build views.
- Aggregations are reliable; analytical patterns (cohort analysis, window functions) require examples.
- AI hallucination still happens; the trust signals exist precisely because not every answer is right.
- Cost: each question is one LLM call + one warehouse query; volume scales with usage.

### Mental model

Genie is **"NL → SQL bounded by curation"**. The product isn't the model — it's the Agent, instructions, metric views, and certified queries. Treat Agent curation as a continuous practice, not a setup task.

---

## Anti-patterns — where you should not use Genie

- **Ask-anything across an entire warehouse.** A single sprawling Agent → poor answers. Build focused agents by domain.
- **Replace BI dashboards entirely.** Dashboards are still better for recurring KPIs; Genie complements them.
- **As a database admin tool.** Genie is for analytical questions, not DDL / DBA tasks.
- **For real-time decisions.** Latency: NL parsing + LLM call + SQL execute → multi-second.
- **Production transactional flows.** Don't put Genie behind a customer-facing app expecting deterministic SQL.
- **With unstable / uncurated data.** Bad schemas + no instructions = bad answers.
- **Without ownership.** An agent without a curator quietly rots.
- **Cross-agent federation.** No first-class "join across agents" — each conversation is one agent.
- **Auditing sensitive flows via NL.** Specific SQL or BI tools beat Genie for compliance-grade auditing.
- **Long-form analytical reports.** Genie is conversational; for narrative reports use notebooks or AI Functions in dashboards.

## Weaknesses — areas Genie does not do well

- **Quality is a function of curation.** No magic. A bad space gives bad answers; a great space gives great answers.
- **Complex multi-table joins.** Without supporting views or examples, Genie struggles.
- **Stateful follow-ups can drift.** "Now do the same for Region B" sometimes misinterprets state.
- **Hallucinated columns / tables.** Less common with bounded schemas, but happens; visible in generated SQL.
- **Limited self-correction.** A bad answer requires user re-phrasing or steward intervention.
- **No first-class explanation.** "Why did you write this SQL?" is partial; not a full chain-of-thought reveal.
- **Per-user RLS enforcement creates per-user variability.** Same question, different users → different answers based on UC policies. Stewards must reason about this.
- **Cost is opaque at scale.** Per-question LLM + warehouse cost adds up; need to attribute via system tables.
- **Chart proposals are basic.** Genie picks a chart type; complex viz needs user edits.
- **Vendor model evolution.** Underlying foundation model can change behavior between Databricks releases.
- **No native A/B testing of instructions.** Hard to know whether instruction change X improved or regressed quality without manual evals.
- **Sensitive to schema names.** Cryptic column names (`f1`, `dt2`) defeat Genie; mitigate via instructions and renames.

---

## Sources

- [AI/BI Genie docs (Genie product family overview)](https://docs.databricks.com/en/genie/index.html)
- [Genie Conversation API — Chat mode and Agent mode](https://docs.databricks.com/en/genie/conversation-api.html)
- [Trusted assets and curation](https://docs.databricks.com/aws/en/genie/trusted-assets)
- [Metric Views](https://docs.databricks.com/en/metric-views/index.html)
- [Glossary](../Glossary/glossary.md)
