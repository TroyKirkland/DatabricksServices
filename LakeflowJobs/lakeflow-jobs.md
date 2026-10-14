# Lakeflow Jobs

> Formerly **Databricks Workflows**. Same product, Lakeflow-umbrella rebrand. Most APIs and the UI still surface "jobs" as the primary noun.

## Level 100 — What it is

Lakeflow Jobs is the **orchestrator** for Databricks. It runs DAGs of *tasks* (notebooks, Python wheels, SQL queries, LDP pipelines, dbt, JARs) on a schedule or in response to a trigger, with retries, notifications, and observability.

L100 takeaways:

- **Native orchestrator** for everything Databricks: notebooks, Python, SQL, LDP, dbt, ML.
- **Triggers**: cron, continuous, file arrival, table update, manual, API.
- **Resilient by default**: per-task retries, repair runs, conditional execution, notifications.

## Level 200 — The product surface

**Core primitives:**

- **Job** — a DAG of tasks with a name, owner, permissions, triggers, run-as identity.
- **Task** — a node in the DAG. Types include: notebook, Python script, Python wheel, JAR, SQL (query/dashboard/file/alert), LDP pipeline (full or selective refresh), dbt, run-job (call another job), if/else, for-each, condition.
- **Dependencies** — declared between tasks; tasks run when all parents complete with the specified status.
- **Triggers** — cron schedule, continuous (re-trigger on completion), file arrival, table update (when a UC table changes), manual.
- **Compute** — per-task choice: job clusters (ephemeral, cheapest DBUs), all-purpose clusters, serverless jobs compute, SQL warehouses.
- **Run-as identity** — a service principal or user; controls UC permissions for the run.
- **Parameters** — job-level and task-level; passed to notebooks via `dbutils.widgets`, to SQL via parameter binding, to wheels via CLI args.
- **TaskValues** — small key/value emit/read between tasks (`dbutils.jobs.taskValues.set/get`).
- **Repair run** — re-run only failed tasks instead of the whole job.
- **Notifications** — email, Slack, Teams, PagerDuty, webhook on start/success/failure/long-running.
- **Authoring** via UI, REST/SDK, Terraform, or **Databricks Asset Bundles** (`databricks bundle deploy`).

## Level 300 — Architecture

```
Trigger ──► Job run ──► Task graph ──► Task runs (each on its own compute)
                       │                ├─ retries / repair scope
                       │                ├─ TaskValues bus
                       │                └─ event/log emission to system.workflow
                       └─ notifications, lineage to UC
```

**Where things run:** the Databricks control plane orchestrates; tasks execute on a compute resource (job cluster, all-purpose, serverless, warehouse) in the workspace. A single job can mix compute types per task.

**Concurrency model:** each task has a `max_retries`, `min_retry_interval_millis`, and timeout. The DAG respects dependency-and-status constraints (`Success` / `All Done` / `At Least One Success` / etc).

**Conditional execution:** `if/else` task picks branches based on `TaskValues` or job parameters. `for_each` fans out a sub-task over a list (parallelism cap configurable).

**State and observability:** job runs and task runs are persistent. Logs land in DBFS / UC volumes per workspace policy. `system.workflow.*` UC system tables expose run history, task duration, and DBU consumption.

## Level 400 — Deep internals

### Run lifecycle

- A job run has a `run_id` (globally unique within workspace). A task run has a `task_run_id` (unique within a job run).
- Job runs are queued briefly when the workspace is over its concurrent-run cap; the cap is per-workspace and adjustable.
- Each task run records `state` (pending/running/skipped/failed/...), `result_state`, start/end times, cluster instance ID, error details.

### Compute choices and DBU implications

- **Job clusters** (ephemeral, per-task or shared across tasks in a job) — cheapest DBU SKU. Start fresh per job run unless task-sharing is configured. Cold-start cost is amortised when tasks share.
- **All-purpose clusters** — most expensive SKU. Avoid for production.
- **Serverless jobs compute** — Databricks-managed, fast startup, scale-to-zero. Different DBU SKU; typically cheaper net once you factor in idle.
- **SQL warehouses** — SQL tasks bill against the warehouse DBU SKU.
- Mixing matters: a job with one wheel task on job cluster + one SQL task on warehouse is fine and common.

### TaskValues mechanics

- Stored in the job run's metadata store, scoped to `<run_id, source_task_key, key>`.
- Read via `dbutils.jobs.taskValues.get(taskKey="...", key="...")`. Throws if upstream task didn't set it (unless `default` is supplied).
- Size cap is small (~48 KB). Don't pass dataframes — pass UC table names instead.

### Retries and repair

- **Retries** rerun a single task within a job run. The DAG above and below retried tasks are unaffected.
- **Repair run** is a new job run that inherits the prior run's state and re-executes only the failed/skipped tasks plus downstream-of-failed. Useful when a transient error broke one branch.
- Triggered failure-only notifications fire on either path.

### Triggers — depth

- **Scheduled (cron)**: quartz-syntax cron expressions; can pin to a specific timezone.
- **Continuous**: at most one run; re-triggers on completion. Used for "always-on" jobs.
- **File arrival**: monitors a UC volume path (or cloud-storage prefix) and triggers on new file matching a pattern. Latency depends on listing/notification mode.
- **Table update**: triggers when a UC table version increments. Useful for "when bronze updates, refresh silver".
- **API**: `POST /api/2.2/jobs/run-now` plus parameter overrides.

### Permissions and run-as

- Each job has a `run_as` identity (service principal recommended for production). All UC permission checks resolve against this identity, not the trigger caller.
- View, manage-run, owner permissions are separate. Service principals must be granted *view* on the job to receive failure emails routed to them.

### System tables

- `system.workflow.job_run_timeline` — one row per job run.
- `system.workflow.job_task_run_timeline` — one row per task run.
- `system.workflow.jobs` — current job definitions.
- These are the source of truth for cost-attribution and SLA dashboards.

### Limits / operational realities

- **1,000 tasks per job** (hard limit). For larger DAGs, factor into sub-jobs called via run-job tasks.
- Per-workspace cap of **2,000 concurrent task runs** (returns 429 when exceeded) and **12,000 saved jobs**.
- `for_each` parallelism cap defaults conservatively; raise it if you need wide fan-out.
- Per-workspace concurrent-run cap; pre-2.2 API requires polling for run state. The 2.2 API supports webhook-style notifications.
- Continuous trigger + a failing task = infinite retry storm; pair with `max_failures` in job settings to bound it.
- Jobs do **not** persist large workspace state. Treat each task as side-effecting on UC, not on /tmp.

### Mental model

Lakeflow Jobs is **a workspace-native Airflow with first-class Databricks task types** and tight UC + system-table integration. Compared to Airflow, you trade extensibility (no `BashOperator` plugins) for native compute provisioning, lineage, and per-run UC permissions.

---

## Anti-patterns — where you should not use Lakeflow Jobs

- **External orchestration of non-Databricks systems.** Jobs is for Databricks tasks. If you orchestrate Airflow + Databricks + Snowflake + custom k8s pods, keep Airflow or Dagster on top and call into Jobs as a subgraph.
- **Heavy fan-out (>1000 parallel tasks).** `for_each` works but the UI gets unwieldy and the run record balloons. Use Spark inside a task to parallelize work instead.
- **Sub-minute event-driven workflows.** Job runs have startup latency (job cluster start ~1–3 min; serverless ~5–15 sec). For sub-second triggers, use Structured Streaming or a Databricks App with a webhook.
- **Long-running interactive sessions.** A job task is a side-effecting batch. If you need a notebook session for a human, use a workspace notebook, not a job.
- **Storing significant state between tasks.** TaskValues are tiny. Pass UC table names, not data.
- **Cron jobs that should be DBSQL alerts.** "Run this query every hour and email me if rows > 0" is a DBSQL Alert, not a job.
- **Branching that depends on data content beyond simple values.** The `if/else` task reads TaskValues/params; for complex branching, do it inside a single notebook task with Python control flow.
- **Cross-workspace orchestration.** A job is workspace-scoped. For multi-workspace, drive from outside (Airflow / Asset Bundles with per-target jobs).
- **Treating job clusters as long-lived shared infrastructure.** They are designed to be ephemeral. For warm shared compute, use a serverless SQL warehouse or a pool.
- **Putting business logic in TaskValues / parameters.** Parameters and TaskValues are knobs, not config systems. Keep meaningful logic in notebooks/wheels.

## Weaknesses — areas Lakeflow Jobs does not do well

- **DAG ergonomics get rough past ~30 tasks.** The graph view is fine but editing large DAGs in the UI is painful. Use Asset Bundles + YAML for serious DAGs.
- **No native conditional skipping with arbitrary expressions.** `if/else` is a single binary; for richer logic you embed it in a notebook.
- **TaskValues are a string KV with low size.** Not a real workflow data passing system. People reach for tiny JSON dumps and regret it.
- **Cluster start dominates short tasks.** A 10-second notebook on a fresh job cluster takes 90 seconds. Pool-backed clusters or serverless mitigate.
- **Repair runs don't always do what you'd hope.** Repair re-executes failed *and downstream*; if a task has expensive successful upstream you can't repair just the one task in isolation easily.
- **For-each results aggregation is awkward.** You can collect counts and outcomes but rich result aggregation requires writing to a UC table within the sub-task.
- **Permissions are coarse.** View / Manage Run / Manage / Owner. No "can edit only this branch" granularity.
- **Logs across tasks are not unified.** Each task has its own driver log, no first-class cross-task tracing.
- **Limited backfill ergonomics.** Re-running a date range over a parameterised job is doable via API but no UI affordance.
- **Cron syntax friction.** Timezone + DST handling can be confusing — make timezone explicit.
- **No native equivalents to dynamic task mapping done at runtime** (à la Airflow `expand`). `for_each` requires the list to be known at job start (computed by an upstream task and emitted via TaskValues).
- **Cost attribution requires system tables.** No first-class per-job DBU widget in UI; build it from `system.billing.usage` and `system.workflow.*`.

---

## Sources

- [Lakeflow Jobs docs](https://docs.databricks.com/aws/en/jobs/)
- [Jobs API 2.2 reference](https://docs.databricks.com/api/workspace/jobs)
- [Conditional / for-each tasks](https://docs.databricks.com/aws/en/jobs/conditional-tasks)
- [System tables: workflow](https://docs.databricks.com/aws/en/admin/system-tables/jobs)
- [Glossary](../Glossary/glossary.md)
