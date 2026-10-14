# AI/BI Dashboards

> Formerly **Lakeview**. Same product, rebranded.

## Level 100 — What it is

AI/BI Dashboards is Databricks' **native dashboarding** product. You define datasets (SQL queries against UC tables/views/MVs), drop widgets (charts, counters, tables) on a canvas, parameterise them, and publish for end users. It's the BI layer that lives inside Databricks, not in Tableau/PowerBI.

- **Lakehouse-native**: queries hit UC tables directly; UC governs access.
- **Modern dashboarding**: cross-filtering, parameters, drill-down, AI/BI Genie integration.
- **Embedded sharing**: publish to viewers without granting them workspace access; subscriptions to PDF / image / link.

## Level 200 — The product surface

- **Dataset** — a named SQL query (against a UC SQL warehouse) that returns rows; or a UC table/view/metric view. Supports multi-statement queries (semicolon-separated). Metric views are a first-class source: reference them with the `MEASURE` aggregate. **Local metric views** can be created scoped to the dashboard without publishing to UC.
- **Custom Calculations** — derived metrics applied on top of an existing dataset without modifying the underlying SQL; useful for dashboard-specific computed columns.
- **Widget types** — line/bar/area/pie/scatter/funnel, counter, table, pivot, filter, text/heading. Each widget binds to a dataset query.
- **Parameters** — user-changeable filters bound to dataset SQL via `:param_name`. Drive cross-filtering and drill-down.
- **Pages** — a dashboard can have multiple pages.
- **Publish vs Draft** — draft mode lets the owner edit; publishing freezes a viewer copy.
- **Genie integration** — link a Genie Agent (formerly Genie Space) to a dashboard for follow-up NL questions.
- **Subscriptions** — scheduled email of dashboard snapshot.
- **Embedding** — share with viewers (paid feature: account-level viewers) without granting workspace access.
- **Asset Bundles** — dashboards are deployable assets in DABs.

## Level 300 — Architecture

```
[UC tables / MVs / STs] ──► [SQL Warehouse] ──► [Dashboard datasets]
                                  ▲                       │
                                  │                       ▼
                                  └── parameter binding ──[Widgets render in browser]
                                                            │
                                                            ├─ Result cache (warehouse)
                                                            ├─ Page cross-filter context
                                                            └─ Genie space (optional)
```

- **All queries run on a SQL warehouse** you select. Pick a serverless warehouse for fast first-load.
- **Result cache** in the warehouse drives instant load on repeat views; first load runs the query.
- **Cross-filtering**: clicking a widget can apply implicit parameter values to other widgets on the page (configurable).
- **Permissions**: dashboard CAN_VIEW vs CAN_EDIT vs CAN_MANAGE. Underlying data queried with the **viewer's identity** or **publisher's identity** (configurable per dashboard).

## Level 400 — Deep internals

### Datasets and query execution

- Each dataset is a SQL query (with optional `LIMIT`) or a reference to a UC table/view/metric view. Supports multi-statement queries. Dataset queries are isolated; widgets reference datasets, datasets reference UC.
- **Custom Calculations** let you add derived columns to a dataset inside the dashboard without altering the source SQL — handy for dashboard-local transforms.
- On publish, the SQL is snapshotted. Edits in draft don't affect viewers until republish.
- Query results stream from warehouse; large results are sampled to fit widget capacity.

### Identity for queries

- **As viewer** — each user's identity runs the SQL. UC row filters / column masks apply per user. Best for fine-grained governance.
- **As publisher** — one identity runs all SQL; viewers see whatever that identity sees. Easiest to make work; weakest for per-user RLS.
- This choice affects audit logs (`system.access.audit`): "as viewer" rows are tagged with the viewer.

### Parameters and cross-filter

- Parameters can be: text, number, date, dropdown (with allowed values from a query), multi-select.
- Default values can be `current_user()`, `current_date()`, etc.
- Cross-filtering implicitly injects predicates into other widgets' dataset queries — only works when the target dataset references the same column the source widget is filtering on.
- Parameters propagate via URL query string for shareable links.

### Caching layers

1. **Result cache** in the warehouse (per dataset query + params + viewer identity). TTL-and-table-version-bounded.
2. **Browser/page state** — current parameter values, cross-filter state. Not durable.
3. **Subscription snapshots** — when a subscription fires, it executes the dashboard once and stores a snapshot artifact for delivery.

### Subscriptions

- Schedule emails to deliver dashboard PDFs, image renders, or links.
- Runs the queries as the configured identity (subscriber or publisher).
- Failures surface as email errors; retry semantics are limited.

### Genie integration

- Each dashboard can link to a Genie Agent (formerly Genie Space) whose tables are the same ones the dashboard uses.
- Users click "Ask Genie" → opens a NL chat scoped to that agent, pre-seeded with the current dashboard context.
- Genie uses the same warehouse for execution.

### Embedding and external viewers

- Account-level viewers feature (paid) lets you share dashboards with users who don't have workspace access.
- iframe embedding for external apps; auth via SSO or a signed-URL flow.
- Embedded usage bills against the publishing workspace.

### Performance characteristics

- First load latency = warehouse-cold-start (5–15s serverless) + slowest dataset query.
- Subsequent loads use result cache when available.
- Filtering / parameter changes re-execute datasets in parallel.
- Heavy dashboards (20+ widgets, complex SQL) noticeably slower; optimise via MVs or pre-aggregated tables.

### Limits / realities

- Widget count per page has a soft cap (~50). Many widgets = many parallel queries on warehouse start.
- Custom visualisations are limited compared to Tableau/PowerBI; no R Markdown / Vega-Lite-level extensibility.
- Drill-through across dashboards is via parameter passing only.
- No first-class write-back to UC from dashboards (no "edit cell" UX).
- Snapshot subscriptions are static images; interactive subscriptions don't exist.

### Mental model

AI/BI Dashboards is **"BI on top of UC + a serverless SQL warehouse"**. Lighter weight than Tableau, deeply integrated with the lakehouse and Genie. Best when your audience trusts the lakehouse as the source of truth and you want governance to live in one place.

---

## Anti-patterns — where you should not use AI/BI Dashboards

- **Complex enterprise BI with hundreds of widgets and bespoke visualisations.** Tableau or PowerBI still beats this. Use AI/BI for lakehouse-centric, focused dashboards.
- **Real-time monitoring (sub-second refresh).** Warehouse query latency floor is in seconds. Use Grafana / a streaming dashboard.
- **Write-back / data entry UX.** Use a Databricks App for forms.
- **Heavy custom visualisations.** Limited chart types vs ecosystem BI tools.
- **As a printable report generator.** PDF snapshots work but layout is web-first, not page-first.
- **Pivot-table-heavy financial modelling.** Pivot widget is basic. Use Excel + UC data exports.
- **Tracking thousands of independent KPI tiles.** Hundreds of small widgets thrash the warehouse.
- **As an ad-hoc analyst sandbox.** Use SQL editor + Genie; dashboards are for distribution.
- **One-off internal report shared in Slack.** A query result paste or a notebook export may be faster.
- **Embedding into external customer apps without account-level viewers.** That feature is the right path; don't iframe a workspace dashboard for end customers.

## Weaknesses — areas AI/BI Dashboards does not do well

- **First-load latency on cold warehouse.** Serverless mitigates but doesn't eliminate.
- **No deep custom visualisations.** Limited to built-in chart types.
- **Cross-dashboard drill-through requires URL parameters.** No first-class navigation.
- **Subscription delivery is static.** No interactive recipient links scoped per user.
- **Parameter UI is limited.** Multi-select works; cascading dropdowns are basic.
- **Versioning lives in DABs, not in-product.** No "see this dashboard as it was yesterday" out of the box.
- **Cross-filter logic surprises.** Auto-inferred predicates don't always match user intent.
- **Identity model has trade-offs.** "As viewer" honours UC but causes more cold queries; "as publisher" is fast but RLS is approximate.
- **Export to Excel is OK, not great.** Some formatting lost.
- **No commenting / annotation.** Discussion happens elsewhere.
- **Dashboard-level cost attribution is hard.** Warehouse query history doesn't tag the source dashboard cleanly.
- **Heavy MV-backed dashboards depend on refresh cadence.** Stale MVs surface to viewers without much warning.

---

## Sources

- [AI/BI Dashboards docs](https://docs.databricks.com/aws/en/dashboards/)
- [Dashboard parameters](https://docs.databricks.com/aws/en/dashboards/parameters)
- [Embedding and sharing](https://docs.databricks.com/aws/en/dashboards/sharing)
- [Subscriptions](https://docs.databricks.com/aws/en/dashboards/subscriptions)
- [Datasets — types and sources](https://docs.databricks.com/en/dashboards/datasets.html)
- [Metric Views](https://docs.databricks.com/en/metric-views/index.html)
- [Glossary](../Glossary/glossary.md)
