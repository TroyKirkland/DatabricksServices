# Databricks Clean Rooms

## Level 100 — What it is

Clean Rooms let **multiple parties run agreed-upon analyses on each other's data without seeing the underlying rows**. Each participant contributes data into a shared, governed environment where only approved queries can run, and only aggregate or otherwise sanitized results come out. Two collaboration models:

- **Approval-based** (no-trust): all collaborators have equal privileges; every notebook must be approved unanimously before it can run.
- **Packaged** (GA July 2026): provider-consumer model — provider supplies hidden code and data; consumer runs analyses against their own data without seeing the provider's code or assets.

- **Multi-party data collaboration**: up to 10 collaborators per clean room join data without exposing raw rows.
- **Approved-query-only** (approval-based) or **trust-the-provider** (packaged): queries are gated per model.
- **UC-native**: built on UC + Delta Sharing primitives; no separate stack.

## Level 200 — The product surface

- **Clean room** — the UC object representing the collaboration. Supports up to **10 collaborators**; locked to the original set after creation.
- **Two collaboration models**:
  - **Approval-based**: all collaborators equal; every notebook must be unanimously approved. Supports **auto-approval rules** to streamline recurring task re-approvals.
  - **Packaged** (GA July 2026): provider-consumer; provider's code + assets hidden from consumer; consumer trusts the provider like a third-party library.
- **Collaborators** — identified by Databricks metastore (D2D), or via Delta Sharing identifier for non-Databricks parties.
- **Contributed assets** — tables, views, and **volumes** (same cloud/region only; volumes not accessible cross-cloud/cross-region). Underlying data stays in the contributor's account; the clean room accesses it via Delta Sharing during approved runs.
- **Tasks / runs** — pre-approved notebooks or queries (approval-based), or provider-authored notebooks run by consumers without code review (packaged).
- **Output bindings** — results from tasks land in a designated output location (each collaborator's UC) for the parties that approved that output.
- **Serverless egress control** — restrict outbound network connections from the sandbox to prevent unauthorized data exfiltration.
- **Differential privacy controls (optional)** — noise injection for aggregate queries.
- **Audit** — every contribution, approval, run, and output is logged.

## Level 300 — Architecture

```
[Org A's UC]              [Org B's UC]
   tables A1, A2             tables B1, B2
        │                         │
        └──── contributes ────────┘
                        ▼
              ┌──────────────────────────┐
              │   Clean Room (UC obj)    │
              │   approved tasks         │
              │   compute (serverless)   │
              └──────────────────────────┘
                        │
              run an approved task
                        ▼
                Results → output binding(s)
                        │
              Org A's output table, Org B's output table
                  (one or both depending on policy)
```

- **Compute** runs in a UC-isolated environment (serverless). Neither party has a shell on it; only the approved code executes.
- **Data flow**: contributor data is read via Delta Sharing into the clean room execution context for the duration of the task. No persistent copy.
- **Result release**: approved outputs are written into pre-agreed UC tables in one or both parties' metastores.
- **Approval workflow**: any new task must be proposed by one party and approved by all collaborators before execution is allowed.

## Level 400 — Deep internals

### Approval model (approval-based rooms)

- Each clean room has a list of **approved task definitions** (notebooks / SQL).
- A task is proposed: contains code, output schema, input asset references.
- All collaborators must explicitly approve before the task is runnable.
- After approval, the task is sealed: edits require re-approval.
- **Auto-approval rules** available: configure conditions under which task updates are auto-approved without manual re-review (approval-based rooms only; not available in packaged rooms).
- Approval is recorded in audit logs with timestamps and identities.

### Packaged clean rooms (GA July 2026)

- **Provider** authors notebooks and contributes data assets; **consumer** contributes their own data.
- Provider's code and data assets are hidden from the consumer — consumer sees only notebook and JAR names, not the code.
- No approval workflow required: consumers run the provider's analyses without reviewing the underlying code (trust-the-provider model).
- Only consumers can view run outputs; providers cannot see consumer data or run results.
- Critical: review the clean room's egress network policy to ensure provider code cannot transmit consumer data externally.

### Execution sandbox

- Approved tasks run on **Databricks-managed serverless compute** with strict network and identity isolation: no egress, no arbitrary library installs (only pre-approved), no console access.
- Code in the task runs as a clean-room-scoped identity that has READ on contributed data and WRITE only to the configured output bindings.
- Logs are minimised: stdout/stderr is suppressed beyond approved structured outputs.

### Differential privacy (when configured)

- Aggregate queries can be wrapped with noise injection to prevent single-record inference.
- Privacy budget tracking: each query draws on an epsilon budget; running out blocks further queries until reset.
- Not always required; many real-world clean rooms run without DP and rely on approval review.

### Output bindings

- An output binding is a (target table, target metastore, schema) tuple.
- A task can produce outputs that go to *some* collaborators and not others (e.g., A sees match counts, B sees match IDs).
- Outputs are written via Delta Sharing under the hood — the recipient sees them as governed UC tables.

### Cross-cloud / cross-region

- Clean rooms work cross-cloud (provider in AWS, consumer in Azure, etc.) via Delta Sharing.
- Compute usually runs in the clean-room owner's region; cross-region data movement pays egress.
- **Volumes are not accessible cross-cloud or cross-region** — only tables in default storage are available in such configurations.

### Limits / realities

- Approval cycles add latency to onboarding new analyses. Plan for review SLAs.
- Code must be reviewable — heavy libraries / opaque ML models are harder to approve.
- Pre-approved libraries set is conservative; bring-your-own typically requires negotiation.
- Result-size limits prevent extracting raw rows disguised as aggregates.
- Audit trails are critical and must be archived for compliance.

### Mental model

A Clean Room is **"a sealed lockbox where two parties' data meets, but only approved code can look inside, and only approved aggregates come out"**. It's the answer to "can we measure overlap / lift / correlation without showing each other our customer lists".

---

## Anti-patterns — where you should not use Clean Rooms

- **Internal use within one org.** Use UC row/column policies + ABAC. Clean Rooms are for cross-org collaboration.
- **Free-form analyst sandboxing.** Every query needs approval. For exploratory work between orgs, define a sandbox space outside clean rooms first.
- **Hot-path / low-latency joins.** Approval cycles + sandbox startup mean clean room queries take minutes, not seconds.
- **Data sets too small / too uniform for privacy.** Tiny datasets can re-identify individuals even with aggregates; DP can't fully save you.
- **Replace a properly drafted DPA or contract.** Clean rooms are technical enforcement; they don't replace legal agreements.
- **Replicating a full data warehouse for the other party.** Use Delta Sharing for that. Clean rooms are for joint analyses, not data delivery.
- **Heavily custom Python with private libraries.** Approval friction dominates value.
- **Cross-cloud at petabyte scale.** Egress costs and complexity explode.
- **One-shot, never-again analyses.** The setup overhead is real. Justified for ongoing collaboration only.
- **As a way to launder PII.** Clean rooms enforce technical constraints, not legal ones. PII rules still apply.

## Weaknesses — areas Clean Rooms do not do well

- **Approval friction.** Every change in code requires re-approval — slows iteration significantly.
- **Library restrictions.** Pre-approved set is conservative; custom libs need explicit allowlisting.
- **Debugging is hard.** Reduced logs + sealed sandbox = limited insight when a task fails.
- **DP budget tracking is conservative.** Aggressive privacy budgets quickly block useful queries.
- **Output schema rigidity.** Outputs are pre-approved; adding a new metric requires a new task.
- **Cross-collaborator scheduling.** Approvals stall on busy reviewers; SLAs are people problems.
- **Limited compute knobs.** Sandbox compute is opinionated; can't tune like a regular cluster.
- **Cross-cloud setup is heavier.** Egress, identity reconciliation, region pairing.
- **Audit volume.** Heavy clean rooms produce a lot of audit events; archive policy needed.
- **No interactive notebook UX.** No live REPL; just submit-approved-task runs.
- **Cost attribution is shared.** Who pays for the sandbox compute is a contract question, not a tool feature.
- **Versioning of approved tasks needs governance discipline.** Otherwise diff between "v1 approved" and "v1.2 in review" is confusing.

---

## Sources

- [Clean Rooms docs](https://docs.databricks.com/aws/en/clean-rooms/)
- [Clean Rooms approval workflows](https://docs.databricks.com/aws/en/clean-rooms/approve-tasks)
- [Packaged clean rooms](https://docs.databricks.com/aws/en/clean-rooms/packaged-clean-rooms)
- [Differential privacy in Clean Rooms](https://docs.databricks.com/aws/en/clean-rooms/differential-privacy)
- [Clean Rooms vs Delta Sharing](https://docs.databricks.com/aws/en/clean-rooms/compare)
- [Databricks July 2026 release notes (Packaged Clean Rooms GA)](https://docs.databricks.com/en/release-notes/product/2026/july.html)
- [Glossary](../Glossary/glossary.md)
