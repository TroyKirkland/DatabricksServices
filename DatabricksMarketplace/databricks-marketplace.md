# Databricks Marketplace

## Level 100 — What it is

Databricks Marketplace is an **open exchange** for data products, AI models, notebooks, and MCP servers. Providers publish listings; consumers (any Databricks workspace or OpenSharing-compatible client) discover, request, and access them. Delivery is via the OpenSharing protocol (built on [[glossary:Delta Sharing]]), so the catalog appears live in the consumer's UC.

- **Open**: OpenSharing/Delta-Sharing-based, so consumers can be Databricks or open-source clients.
- **Data + AI + notebooks + MCP servers**: not just tables — datasets (tabular and non-tabular volumes), models, notebooks, MCP servers.
- **UC-native** for Databricks consumers; foreign catalog appears with one click.

## Level 200 — The product surface

- **Provider** — an org publishing listings; gets a "provider page" in Marketplace.
- **Listing types**:
  - **Datasets** — tabular data (UC catalogs) and non-tabular data (Databricks Volumes).
  - **AI/ML models** — registered UC models shared via OpenSharing for D2D consumers.
  - **Notebooks** — runnable code samples that demonstrate the data product.
  - **MCP servers** — tools and APIs agents can use to interact with external systems.
- **Consumer** — a workspace or generic OpenSharing/Delta Sharing recipient accessing listings. Marketplace is browsable publicly without a workspace.
- **Access models**:
  - **Free** — public, no approval.
  - **Free with approval** — provider gates per request.
  - **Private exchange** — invite-only listings.
  - **Paid** — billing managed through Marketplace + your cloud marketplace (AWS/Azure/GCP); eligible purchases can draw from **Universal Commit Drawdown** (up to 10% of Universal Commits).
- **Get access** — consumer requests; provider approves; UC catalog appears as `<provider>.<share>.<table>`.
- **Search and discovery** — filter by category, industry, free/paid, region.

## Level 300 — Architecture

```
[Provider workspace]                       [Marketplace catalog]                    [Consumer workspace]
  UC tables / models / notebooks  ─►   listing metadata     ◄────  search/request   │
  + volumes (non-tabular) + MCP    ─►   approval policy                              │
  + share definition                                                                 │
                              once approved                                         │
                                                                                    ▼
                                                              Foreign UC catalog backed by OpenSharing protocol
                                                                (MCP server: installed as workspace connection)
                                                                                    │
                                                                                    ▼
                                                                              Consumer queries / uses
```

- **Listings are backed by the OpenSharing protocol** (built on Delta Sharing): provider exposes data via a share; Marketplace adds discovery + metadata + approval workflow.
- **Non-tabular data** (images, documents, binaries) is delivered via Databricks Volumes rather than Delta tables.
- **MCP server listings** are installed as workspace connections rather than as a UC catalog.
- **Approval workflow** is provider-managed; can be automatic for free listings.
- **Consumer side**: a single click creates a foreign UC catalog mapped to the share.
- **Public browsing**: the Marketplace catalog is browsable without a Databricks workspace.

## Level 400 — Deep internals

### Provider mechanics

- A provider has a profile page (logo, contact, listings list).
- Each listing references one or more Delta Sharing shares (`shares: [my_share]`).
- Listings declare which tables / models / notebooks are included.
- Metadata: tags, industry, geography, schema preview.

### Consumer mechanics

- Browse Marketplace → request access.
- For free listings: instant catalog creation.
- For approval-required: provider gets a request; on approval, consumer can install.
- Installation: `CREATE CATALOG ... USING SHARE` happens automatically; appears in UC.
- Permissions: consumer's catalog uses `consumer.*` privileges; provider audits via UC outbound logs.

### Paid listings

- Subscription / consumption-based billing handled via your cloud's marketplace (AWS Marketplace, Azure Marketplace, GCP Marketplace) using a connected billing account.
- Usage metering: providers configure how usage is counted (rows queried, models inferenced, etc.).
- **Universal Commit Drawdown**: Databricks customers can use up to 10% of their Universal Commits balance to pay for eligible third-party Marketplace listings, reducing the need for separate cloud-marketplace billing.

### Notebooks, AI models, and MCP servers in listings

- Notebooks: bundled with sample queries / tutorials; downloaded as part of access.
- AI models: registered models in UC shared via OpenSharing/Delta Sharing for D2D consumers; consumers can deploy locally to Model Serving.
- MCP servers: provide tools and APIs for agents; installed as workspace connections so agent frameworks can call them directly.
- Non-tabular data: delivered via Databricks Volumes (file-level access, not Delta tables).

### Search and discovery

- Curated categories (Healthcare, Finance, Retail, etc.) and filters.
- Provider-set tags help discovery.
- "Featured" placement managed by Databricks.

### Auditing

- Provider side: `system.access.outbound_*` shows consumer-by-consumer usage.
- Consumer side: `system.access.audit` shows queries against the foreign catalog.

### Limits / realities

- Pre-signed URL semantics from Delta Sharing apply: revocation isn't instant.
- Provider egress costs scale with consumer usage; paid listings should price-in egress.
- Schema changes on shared tables affect all consumers; notify proactively.
- Marketplace UI search is OK but not deep — for complex requirements, contact providers directly.
- Currency / billing for paid is per-cloud-marketplace; cross-cloud paid listings are awkward.
- Some advanced UC features (row filters, masks) work over sharing with caveats.

### Mental model

Marketplace is **"a discovery layer on top of OpenSharing/Delta Sharing, with optional billing"**. The technical heavy lifting is Delta Sharing's; Marketplace adds the catalog, search, approval flow, MCP server distribution, and (for paid) billing integration including Universal Commit Drawdown.

---

## Anti-patterns — where you should not use Marketplace

- **Sharing within a single org.** Use UC + Delta Sharing directly; Marketplace is for discoverability across orgs.
- **Real-time data sharing.** Pre-signed URL + Delta Sharing latency is seconds/minutes-class.
- **Workflow that needs strict cross-tenant isolation.** Marketplace shares are read access to live data; use Clean Rooms for joint computation.
- **Listings that change schema frequently.** Consumers will see breakage.
- **Reverse-distribute outputs to many consumers without governance.** Delta Sharing is point-to-point; cross-cutting changes have ripple.
- **High-throughput operational data exchange.** Marketplace is for discovery + delivery, not OLTP-style data flow.
- **Distributing PII without proper legal frameworks.** Marketplace doesn't substitute for contracts.
- **Models that depend on tightly coupled inference infrastructure on the consumer side.** Provide self-contained models, or detailed deployment notebooks.
- **Sales-led complex enterprise data products.** Use direct contract + Delta Sharing, not Marketplace.
- **Listings without sample queries or documentation.** Consumers can't evaluate.

## Weaknesses — areas Marketplace does not do well

- **Search is basic.** Hard to find niche listings.
- **Revocation isn't instant.** Inherited Delta Sharing pre-signed URL behavior.
- **Cross-cloud paid billing is awkward.** Cloud marketplaces are separate worlds.
- **No first-class versioning of listings.** Schema changes propagate; consumer protections are weak.
- **Discovery analytics for providers are limited.** Hard to see who looked at the listing vs requested.
- **Consumer-side cost from queries.** Querying a Marketplace catalog costs consumer warehouse DBUs; not always obvious upfront.
- **Provider-side egress visibility is meh.** Track via outbound audit + cloud egress bills.
- **Notebook updates require new listing or replace.** No first-class notebook versioning.
- **Cross-listing dependencies aren't modelled.** "This listing pairs with that listing" is in the metadata, not enforced.
- **License attestation is text-based.** No automated license-acceptance gating.
- **Per-region listings.** Cross-region requires re-sharing.
- **Paid listing pricing model is rigid.** Common patterns supported; bespoke pricing needs custom contracts.

---

## Sources

- [Databricks Marketplace docs](https://docs.databricks.com/aws/en/marketplace/)
- [Glossary](../Glossary/glossary.md)
