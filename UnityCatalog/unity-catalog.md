# Unity Catalog (UC)

## Level 100 — What it is

Unity Catalog is Databricks' **unified governance plane**. One metastore per region per account governs tables, views, models, volumes, ML features, functions, MVs, streaming tables, and external sources — across all workspaces. Permissions, lineage, audit, and discovery live in UC.

- **One metastore**, many workspaces.
- **Three-level namespace**: `catalog.schema.object`.
- **Fine-grained**: GRANTs, row filters, column masks, attribute-based access control (ABAC).
- **First-class lineage and audit**: every read/write logged.

## Level 200 — The product surface

- **Metastore** — top-level container, one per region per Databricks account.
- **Catalog** — top namespace level; e.g., `production`, `dev`, `sandbox`.
- **Schema** (a.k.a. database) — mid level; `<catalog>.<schema>`.
- **Object types**: tables, views, materialized views, streaming tables, volumes, functions, models, ML features, connections, foreign catalogs, clean rooms, services credentials, **managed and foreign Iceberg tables** (GA May 2026), **secrets** (`catalog.schema.secret`, GA August 2026).
- **External locations** + **storage credentials** — UC objects letting catalogs/tables reference cloud storage outside the metastore root.
- **Permissions**: `GRANT SELECT/MODIFY/USAGE/...` on UC objects. Inheritance from catalog → schema → object.
- **Row filters** + **column masks**: SQL UDFs applied at query time per identity.
- **Attribute-Based Access Control (ABAC)** — policy rules based on tags/attributes; scaled approach to row/column access.
- **Lineage**: table-to-table and column-level lineage for queries that hit UC.
- **System tables** — `system.*` catalog with billing, audit, lineage, query history, access history.
- **Discovery** — Catalog Explorer UI; comments and tags on objects; AI-generated descriptions.
- **Sharing** — Delta Sharing share / recipient / providers.
- **Federation** — foreign catalogs from connections.

## Level 300 — Architecture

```
       Account-level Databricks console
                  │
                  ▼
        ┌─────────────────────┐
        │  UC Metastore (1)   │ ── stores: catalog/schema/object metadata,
        │  per region         │           credentials, grants, policies,
        │                     │           lineage events, audit refs
        └─────────────────────┘
            ▲             ▲
   workspace W1     workspace W2    ... (many workspaces attach)
            │             │
    queries from clusters / warehouses / jobs / apps
            │
            ▼
    UC enforces: permission, row filter, column mask, ABAC
            │
            ▼
    Storage credentials → cloud object storage (managed or external)
```

- **Metastore** is the source of truth for everything UC-governed. Workspaces attach to a metastore at provisioning.
- **Query path**: any read/write from a cluster/warehouse goes through UC permission checks before storage I/O is allowed.
- **Storage credentials** are UC's bridge to cloud IAM (instance profile / managed identity / service account).
- **Lineage events**: every UC query emits an event to a lineage subsystem; surfaced in Catalog Explorer and `system.access.column_lineage` / `table_lineage`.
- **Audit**: every UC operation (grant change, query, schema change) logs to `system.access.audit`.

## Level 400 — Deep internals

### Object model and naming

- **3-level naming**: `<catalog>.<schema>.<object>`. Workspace-local "default catalog" exists for compatibility (set via cluster property).
- **Identifiers** are case-insensitive on access, case-preserving on creation.
- **Object IDs** are stable UUIDs; renames preserve the ID and lineage.
- **Comments and tags** are first-class metadata; AI-generated descriptions can pre-fill comments.

### Permission model

- **Privileges**: SELECT, MODIFY, USE CATALOG, USE SCHEMA, CREATE TABLE, EXECUTE, BROWSE, READ VOLUME, WRITE VOLUME, etc.
- **Fine-grained DML privileges** (Beta, DBR 18.1+): INSERT, UPDATE, DELETE are now separate privileges — least-privilege alternatives to the broader MODIFY grant.
- **READ METADATA privilege** (GA): grants read-only visibility into security-sensitive metadata including grants, row filters, column masks, and ABAC policies; intended for auditors and governance teams.
- **Inheritance**: granting USE CATALOG on `cat1` is a prerequisite for using anything under it; SELECT on `cat1.sch1.t1` requires SELECT on the table plus USE on the catalog and schema.
- **Group-based**: grants on workspace groups, account groups, service principals, users.
- **Role-Based Access Control (RBAC)** (GA August 2026): users can assume a named role, applying only that role's permissions rather than their full accumulated privileges — enables clean exclusive-access patterns for sensitive data.
- **Ownership**: each object has an owner; owner has full permissions and can grant.

### Row filters and column masks

- **Row filter**: a SQL UDF that returns boolean; applied at read time. `CREATE ROW FILTER fn(...) RETURN ...; ALTER TABLE t SET ROW FILTER fn ON (col)`.
- **Column mask**: a SQL UDF that transforms a column value at read time. Common pattern: `IF(is_member('hr'), col, NULL)`.
- Evaluated on every read; can be performance-sensitive on big scans. Use cheap functions and rely on `current_user()` / `is_account_group_member(...)`.

### Attribute-Based Access Control (ABAC)

- Policy language over **tags** (on objects and identities).
- A policy says, e.g., "grant SELECT to identities with tag `clearance=secret` on tables with tag `classification=secret`."
- **Attachment hierarchy**: policies can be attached at catalog, schema, or table level — and now at the **metastore level** (Beta) so a single policy applies across every catalog in the account.
- **DENY policies** (Beta): in addition to GRANT policies, DENY can now block the `MANAGE ACCESS CONTROL` privilege on supported securables.
- **Identity Attributes** (Beta August 2026): up to nine IdP attributes (department, country, etc.) stored on users and available in ABAC column-masking policies, synced via SCIM 2.1 or manual management.
- **AI asset coverage** (Beta August 2026): ABAC GRANT policies now apply to models, model services, model provider services, MCP services, and agent services — not just data objects.
- Scales better than per-table grants for large taxonomies.
- Combines with row filters / column masks for fine-grained behaviours.

### Storage credentials and external locations

- **Storage credential** — wraps an IAM role / managed identity / service account.
- **External location** — `(URL, credential)` pair; UC enforces that table create / write under that URL requires `CREATE EXTERNAL TABLE` on the location.
- This is how UC enforces "you can't just write to a random S3 bucket" — even cluster IAM is fronted by UC checks.

### Lineage

- Column- and table-level lineage from all UC queries.
- Surfaced in Catalog Explorer; queryable via `system.access.column_lineage` / `table_lineage`.
- **Model service lineage**: UC now captures lineage for model services and external model providers via AI governance — tracks which data assets feed which served models.
- **External asset lineage**: upstream sources (Salesforce, MySQL) and downstream consumers (Tableau, Power BI) can be linked for end-to-end lineage graphs.
- **Genie Code integration**: natural language lineage queries via `/getTableLineages` and `/getTableInsights` commands.
- Lineage is asynchronous: events land within minutes; gaps can occur during outages.
- Lineage stops at foreign edges (federation, Delta Sharing recipients).

### System tables

- `system.access.audit` — every UC operation.
- `system.access.table_lineage`, `column_lineage` — lineage events.
- `system.billing.usage`, `system.billing.list_prices` — cost.
- `system.compute.warehouses` / clusters — runtime state.
- `system.query.history` — DBSQL queries. **Query text is now masked by default** (August 2026); members of `databricks_pii_access` group see unmasked text.
- `system.workflow.*` — jobs.
- `system.tags.governed_tags` (Beta July 2026) — programmatic access to governed tag keys across an account.
- Each is a managed Delta table with retention.

### Volumes and non-tabular data

- **UC Volumes** govern non-tabular data: PDFs, images, model artifacts, init scripts.
- Path scheme `/Volumes/<cat>/<sch>/<vol>/...` works in code (Python `open()`, Spark, dbutils.fs).
- Permissions: READ VOLUME, WRITE VOLUME, MANAGE VOLUME.

### Multi-workspace governance

- One metastore can serve many workspaces. Each workspace can bind to specific catalogs via **workspace bindings**, allowing dev/prod separation while sharing the metastore.
- Catalog can be **isolated** (single workspace) or **shared** (multiple).
- Service principals can be workspace-local or account-scoped.

### Managed vs external tables

- **Managed**: data in the metastore-managed root; UC owns the files; supports Predictive Optimization.
- **External**: data at an external location; UC tracks metadata but cloud lifecycle is yours.
- Migration: `CONVERT TO MANAGED` (with restrictions).

### Limits / realities

- One metastore per region per account: cross-region requires cross-metastore plumbing (Delta Sharing).
- Some legacy workloads still on Hive Metastore (HMS); migration is a project.
- Token-based privilege caching: grants take seconds to propagate.
- Catalog Explorer scales well but searching across 100K+ tables can lag.
- ABAC complexity grows quickly without a tagging discipline.

### Mental model

UC is **"IAM + lineage + audit for the lakehouse"**, with first-class fine-grained access control. Build your governance once at the metastore level, project it everywhere. The biggest design decision is your catalog/schema taxonomy — it's hard to undo at scale.

---

## Anti-patterns — where you should not use UC (or design choices to avoid)

- **Skipping UC on new workspaces.** Hive Metastore is legacy. Start in UC.
- **One giant catalog with thousands of schemas.** Hard to govern. Use catalog-per-domain or catalog-per-environment.
- **Per-table grants for hundreds of users.** Use groups and/or ABAC.
- **Row filters that join to other tables.** Performance death. Use simple identity-based predicates.
- **Catalogs as environments via name suffix.** Use workspace bindings instead of `prod_x`, `dev_x` naming.
- **External tables when managed would work.** Managed gets Predictive Optimization; external is your maintenance problem.
- **Hardcoded service-principal-only access patterns.** Mix in groups for human discoverability.
- **Storing huge non-tabular blobs as table columns.** Use UC Volumes.
- **Cross-region single-metastore architecture.** One per region; use Delta Sharing across.
- **Treating UC as documentation.** It's enforcement; comments and tags are nice-to-have, governance is the point.

## Weaknesses — areas Unity Catalog does not do well

- **Cross-region multi-metastore management.** No single pane across regions; Delta Sharing bridges but adds overhead.
- **ABAC complexity scales fast.** Tag taxonomies need maintenance; conflicts are tricky to debug.
- **Row filter / column mask performance.** Heavy UDFs in masks/filters hurt scan speed.
- **Permission propagation isn't instant.** Seconds, sometimes longer; CI tests need retry logic.
- **Lineage is async + gappy.** Outages cause holes; not realtime.
- **HMS migration is real work.** Tables, views, paths, permissions all need re-creation.
- **Connection / credential rotation tooling is basic.** Rotating a Snowflake password = recreate connection.
- **Tagging is a wild west without governance.** Pick a tag taxonomy or you'll regret it.
- **Catalog Explorer UI scales OK, not great.** 100K+ tables means slow browsing.
- **System tables retention is finite.** For >12-month audit, archive to your own table.
- **Federation lineage is limited.** Source-side queries / changes don't propagate.
- **Workspace-vs-account identity rules can confuse.** Workspace-local SPs vs account SPs behave differently in some flows.

---

## Sources

- [Unity Catalog docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/)
- [System tables](https://docs.databricks.com/aws/en/admin/system-tables/)
- [Row filters and column masks](https://docs.databricks.com/aws/en/data-governance/unity-catalog/row-and-column-filters)
- [ABAC docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac)
- [Lineage](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage)
- [Databricks August 2026 release notes](https://docs.databricks.com/en/release-notes/product/2026/august.html)
- [Databricks July 2026 release notes](https://docs.databricks.com/en/release-notes/product/2026/july.html)
- [Glossary](../Glossary/glossary.md)
