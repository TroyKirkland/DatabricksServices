# Declarative Automation Bundles (DAB)

_Formerly: Databricks Asset Bundles._

## Level 100 — What it is

DABs are **infrastructure-as-code for Databricks workloads**. You define jobs, pipelines, dashboards, ML pipelines, apps, schemas — anything in the workspace — as YAML, version it in Git, and deploy with `databricks bundle deploy`. Per-environment overrides via *targets*. CI/CD that actually fits Databricks.

- **YAML manifest** (`databricks.yml`) per project.
- **Targets** for dev / staging / prod with overrides.
- **Resources** are Databricks objects: jobs, pipelines, dashboards, apps, models, schemas, MLflow experiments, and dozens more — including model serving endpoints, vector search, clusters, Lakebase databases, Genie spaces, MCP services, and secret scopes.

## Level 200 — The product surface

- **`databricks.yml`** at project root — names the bundle and lists targets + includes.
- **Resources** declared under `resources:` — now 40+ types, including:
  - `jobs:` Lakeflow Jobs.
  - `pipelines:` LDP pipelines.
  - `models:` MLflow registered models in UC.
  - `experiments:` MLflow experiments.
  - `dashboards:` AI/BI Dashboards.
  - `apps:` Databricks Apps.
  - `schemas:` / `volumes:` / `catalogs:` / `external_locations:` UC objects.
  - `alerts:` DBSQL alerts.
  - `clusters:` / `instance_pools:` / `cluster_policies:` / `sql_warehouses:` compute.
  - `model_serving_endpoints:` Model Serving.
  - `quality_monitors:` UC table monitors.
  - `vector_search_endpoints:` / `vector_search_indexes:` Vector Search.
  - `genie_spaces:` Genie agents.
  - `lakebase_*` resources (Lakebase Autoscaling and Provisioned databases, branches, roles, etc.).
  - `mcp_services:` / `model_provider_services:` MCP and model provider endpoints.
  - `secret_scopes:` Databricks secrets.
- **Targets** — environment definitions (`dev`, `staging`, `prod`) with workspace URL, run-as identity, variable values, and per-target overrides of resources.
- **Variables** — typed inputs (`type: string`, default value, prompt for interactive use; `type: complex` for structured values).
- **Includes** — split big bundles into multiple files for sanity.
- **CLI**: `databricks bundle validate / deploy / run / destroy / sync / generate`. Requires Databricks CLI v0.218.0+.
- **Python bundles**: bundles can be defined programmatically in Python as an alternative to YAML.
- **Workspace-only mode**: if all artifacts live in workspace, no local CLI install required.
- **CI integrations**: GitHub Actions, Azure DevOps, GitLab.

## Level 300 — Architecture

```
[Project repo]
  ├ databricks.yml
  ├ src/  (notebooks, wheels, sql files)
  └ resources/  (modular yaml)
        │
        ▼
  databricks bundle deploy --target prod
        │
        ▼
  ┌─────────────────────────────────────┐
  │  Bundle CLI                          │
  │  ├ Validate YAML                     │
  │  ├ Resolve variables + target merge  │
  │  ├ Upload source artifacts (UC vol)  │
  │  ├ Upsert resources via REST APIs    │
  │  └ Output deployment summary         │
  └─────────────────────────────────────┘
        │
        ▼
  Databricks workspace (jobs, pipelines, dashboards, apps, models all reconciled)
```

- **State** lives in a UC volume (`.bundle/state` by default) so the CLI knows what was deployed before.
- **Idempotent**: re-running `deploy` only updates what changed.
- **Run-as identity** for each resource is configured per target.

## Level 400 — Deep internals

### Manifest structure and merging

- A bundle is `databricks.yml` + included files. Inclusion order matters; later includes override earlier ones.
- Targets merge on top of the base resources: target `prod` overrides specific keys (e.g., `cluster.num_workers`, schedule, run-as).
- Variables substitute via `${var.foo}`; resolved per target.
- Built-in variables: `${workspace.current_user.userName}`, `${bundle.target}`, `${workspace.file_path}`.

### Resource model

- Each resource is a typed object with the same schema as the underlying REST API (Jobs, Pipelines, etc.).
- Resources can reference each other: a job task `pipeline_task.pipeline_id = ${resources.pipelines.my_pipeline.id}`.
- Cross-resource references resolved at deploy.

### Deployment lifecycle

- `validate`: parses + type-checks + dry-runs (no calls to workspace).
- `sync`: uploads source files (notebooks, wheels, dashboard JSON, SQL files) to the workspace file system / UC volumes.
- `deploy`: applies resource diffs via REST APIs; preserves existing resource IDs by name.
- `run`: triggers a Job or pipeline defined in the bundle.
- `destroy`: removes all resources owned by the bundle.
- `generate`: reverse-engineers YAML from an existing workspace resource (Job, pipeline, etc.) — useful for onboarding UI-created resources into DAB control.

### State and ownership

- The bundle's state tracks what it created. Resources not in YAML but managed by the bundle are removed on `deploy`.
- Resources outside the bundle (created in UI) are left alone unless their name conflicts.
- Multiple bundles can coexist in one workspace as long as resource names don't collide.

### Per-target overrides

- Cluster sizes / serverless modes / schedules can differ per target.
- Source paths can differ (e.g., dev branch vs main).
- Permissions can differ: dev gives the team CAN_MANAGE, prod restricts to a service principal.

### Variables, secrets, parameters

- Variables: in YAML, typed, defaults, prompts.
- Secrets: referenced via Databricks Secret Scopes; YAML holds `{{secrets/scope/key}}` placeholders evaluated at run time.
- Workspace parameters (e.g., catalog name) can be set per target.

### Source artifacts

- Notebooks: declared as `notebook` tasks; paths relative to bundle root.
- Wheels: built locally or in CI, uploaded.
- SQL files: deployed and referenced by Job tasks.
- Dashboards: exported JSON checked in to YAML.

### CI/CD pattern

- PR: `bundle validate` in CI.
- Merge to main: `bundle deploy --target staging`.
- Manual gate: `bundle deploy --target prod` with prod credentials.
- Rollback: re-deploy previous commit.

### Limits / realities

- DABs cover a wide swath of resource types but not 100% — some preview features lag.
- Cross-bundle dependencies aren't first-class; coordinate via shared resources / Catalog or via top-level orchestration.
- State is per bundle per target; renaming a resource is treated as delete-then-create unless you provide migration hints.
- Large bundles slow down `deploy`; modularise.
- Permissions on bundles themselves (who can deploy) are workspace-level (group membership on CI service principal).
- Notebook source format gotcha: `.py` (Databricks notebook) vs `.ipynb`; declare correctly.

### Mental model

DABs (Declarative Automation Bundles) are **"Terraform for Databricks workloads"**, scoped to workspace assets you'd otherwise build via UI clicks. The deploy command is reconciler-style; YAML (or Python) is the source of truth. Major lever: cleanly separate base resources from per-target overrides.

---

## Anti-patterns — where you should not use DABs

- **Building a workspace from scratch.** Use Terraform for workspace-level resources (network, metastore, identity); DABs deploy *into* a workspace, not the workspace itself.
- **Single one-off jobs.** A notebook + manual schedule is faster.
- **As config storage for app runtime.** YAML is at deploy time, not runtime.
- **Storing secrets in `databricks.yml`.** Use Secret Scopes.
- **One giant bundle for the whole org.** Modular bundles per team/domain scale better.
- **Cross-environment resource references.** Each target should be self-contained.
- **As a workflow language.** It declares resources; Lakeflow Jobs orchestrate them.
- **Treating bundle state as immutable.** State can drift if people edit in UI; reconcile regularly.
- **Skipping `bundle validate` in CI.** Catch errors before deploy.
- **Auto-destroy of prod from a CI fail-safe.** `destroy` is destructive; gate carefully.

## Weaknesses — areas DABs do not do well

- **Some resource types still preview / partial.** Coverage gaps documented; check before commit.
- **Cross-bundle dependencies.** No first-class import / namespace.
- **Schema migration ergonomics.** Renaming a resource = delete-then-create unless you script around it.
- **Limited templating beyond variables.** No conditionals / loops in YAML; for complex logic, generate YAML from code.
- **No first-class plan/diff output.** `deploy` prints summary; some folks want Terraform-style explicit plan.
- **UI-edited resources drift.** Anyone clicking in the UI un-syncs reality from YAML.
- **Local dev experience could be better.** Iterating on YAML is slower than clicking.
- **Notebook diffs in PRs are ugly.** JSON / proprietary formats; use `.py` source format.
- **Secret references vs literal strings can confuse newcomers.** Read the docs.
- **Permissions modelling has rough edges.** Per-resource grants get verbose.
- **Limited unit-test framework for the YAML logic.** Validate-only catches syntax, not intent.
- **Bundle CLI version pinning.** Major CLI versions can break older bundles; CI should pin.

---

## Sources

- [Declarative Automation Bundles docs](https://docs.databricks.com/aws/en/dev-tools/bundles/)
- [Resource reference](https://docs.databricks.com/aws/en/dev-tools/bundles/reference)
- [Resources supported in bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/resources)
- [Targets and variables](https://docs.databricks.com/aws/en/dev-tools/bundles/settings)
- [CI/CD with bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/ci-cd)
- [Glossary](../Glossary/glossary.md)
