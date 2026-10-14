# Delta Sharing

## Level 100 — What it is

Delta Sharing is an **open protocol** for sharing live data between organizations without copying the data. The provider exposes Delta tables; the recipient queries them through a thin REST API that returns short-lived pre-signed URLs to the underlying Parquet files. Reads happen directly on cloud storage.

- **Open protocol**: spec is public; any client can implement it.
- **Live data, no copy**: recipient queries the provider's actual files at read time.
- **Two flavors**: Databricks-to-Databricks (D2D, full UC integration) and Databricks-to-Open (D2O, generic recipient with token).

## Level 200 — The product surface

- **Share** — a UC securable that bundles tables / views / volumes / notebooks / models / MVs you want to publish.
- **Recipient** — a securable representing who can consume a share. Either a Databricks workspace (D2D, identified by metastore ID + cloud) or a generic identity (D2O, identified by a bearer token).
- **Recipient profile** (D2O) — a JSON file containing the sharing server endpoint and the bearer token. Recipients pass it to the open-source `delta-sharing` Python/Spark client.
- **Grant** — connecting a share to a recipient.
- **Read paths**:
  - D2D: recipient sees the share as a UC catalog (`PROVIDER` / `SHARE` model). Queries via SQL or DataFrame.
  - D2O: recipient uses the open `delta-sharing` library to list and load tables.
- **Filtering and CDF**: providers can share with CDF enabled; recipients can read incremental changes via `table_changes`.
- **Partition filtering**: recipients can push down predicates; only matching files get URL-signed.

## Level 300 — Architecture

```
[Provider Databricks workspace]                                     [Recipient]
  UC managed tables                                                    │
  + Share definition                                                   │
        │                                                              │
        ▼                                                              ▼
  Delta Sharing server (control-plane REST API)  ◄────── auth + list/query ───────  D2D UC catalog
        │                                                              OR
        ▼                                                              D2O delta-sharing client
  Pre-signed URLs to Parquet files in provider's cloud storage  ◄── direct read ─── recipient compute
```

- **Two channels**: a thin REST control plane (lists tables, returns file URLs) and the bulk data plane (direct cloud-storage reads via pre-signed URLs).
- **No engine on the provider side** — the recipient does all the compute. The provider only signs URLs and meters access.
- **Pre-signed URLs** have short lifetimes (typically minutes); the recipient must refresh as needed.
- **D2D**: piggybacks on UC. Tables show up as a foreign catalog; permissions, lineage at the recipient. Audit lives in UC `system.access.*`.
- **D2O**: relies on bearer token; the recipient profile encodes the endpoint URL + token. Auditing is provider-side via the Delta Sharing server logs.

## Level 400 — Deep internals

### Wire protocol

- REST endpoints documented in the open spec: `/shares`, `/schemas`, `/tables`, `/query`.
- The `/query` endpoint accepts predicates and returns a paginated list of file objects with pre-signed HTTPS URLs.
- File URLs are unique per request; consumer caches them but they expire (default 1 hour for D2D, configurable).

### D2D (Databricks-to-Databricks)

- Provider creates `SHARE` containing tables; grants to recipient (identified by recipient metastore ID + cloud + region).
- Recipient creates a UC **catalog from the share**: `CREATE CATALOG <name> USING SHARE <provider>.<share>`.
- Tables surface as read-only in recipient's UC. Permissions managed via the catalog; usage audited in UC system tables.
- Authentication uses cloud-native identity (not bearer token) — relies on Databricks-to-Databricks workspace trust.

### D2O (Databricks-to-Open)

- Provider creates recipient with **token-based** auth; emits a recipient profile JSON.
- Recipient uses the OSS `delta-sharing` library (Python, Java/Scala via Spark) to read tables.
- Token lifecycle: rotation, expiry, and audit are provider-side responsibilities. Token revocation is immediate.

### Pre-signed URL mechanics

- AWS: pre-signed S3 GET URLs; Azure: pre-signed Blob SAS URLs; GCP: signed URLs.
- Pre-sign uses the provider's storage credential — recipients never see provider IAM.
- Pre-signed URLs can be intercepted; treat them as bearer credentials for the file's lifetime. Use HTTPS-only.

### CDF over sharing

- If the provider table has CDF enabled and the share includes CDF, recipients can call `SELECT * FROM table_changes('<share-cat>.<schema>.<table>', startV, endV)`.
- Enables incremental consumption — useful for downstream LDP `APPLY CHANGES INTO` pipelines.

### Filtering pushdown

- Partition filters and predicates push to the provider's sharing server, which only signs URLs for matching files.
- Provider tables clustered with liquid clustering can prune effectively even without partition filters; the server uses Delta stats to skip files.

### Provider-side controls

- **Token / recipient IP allow-lists** (D2O).
- **Recipient-level read quotas** (workspace-level rate-limit).
- **Per-share auditability** in UC system tables (`system.access.outbound_*`).
- **Revocation** via `DROP RECIPIENT` or share grants; effect within a few minutes once token TTL elapses.

### Limits / realities

- Pre-signed URLs imply a recipient can keep reading files until the URL expires, even after share revocation. Plan accordingly.
- Sharing of streaming tables / MVs is supported with caveats; some maintenance operations (FULL REFRESH on the source) can disrupt active recipient queries.
- Views: only Delta-Sharing-compatible views (Spark SQL, no UDFs); complex views fail.
- Notebooks/AI models in shares are D2D-only.
- Bandwidth/egress costs accrue to the **provider** (recipient reads from provider storage).

### Mental model

Delta Sharing is **"Parquet over HTTPS, brokered by a tiny server"** — the recipient gets pre-signed URLs and reads directly. UC layers governance on top (D2D) or you fall back to a bearer token (D2O). The mental shift from FTP-style data shipping is that there is *no copy*; queries hit live data, and the provider continues to OPTIMIZE/VACUUM without notifying recipients.

---

## Anti-patterns — where you should not use Delta Sharing

- **Sharing across regions with high egress sensitivity.** Recipient reads from provider storage; egress on the provider scales with recipient queries.
- **Real-time low-latency data feeds.** Pre-sign + REST round-trip + file fetch is on the order of seconds, not milliseconds.
- **Sharing transactional / OLTP data.** Sharing snapshots a Delta table; rapidly-mutating sources cause version skew.
- **Shares that require row-level security for recipient X.** Row filters apply at the *provider's read*; sharing doesn't natively project them per-recipient. Use Clean Rooms for fine-grained controlled access.
- **Internal-only data movement between Databricks workspaces in the same metastore.** UC already lets you grant directly. Sharing is for cross-metastore.
- **Sharing non-Delta data.** It's Delta Sharing for a reason.
- **Pushing pre-signed URLs around.** They are read-credentials. Don't ship them in emails.
- **Long-lived recipient sessions reading 24/7.** Egress + URL re-sign load. Provide a refresh cadence.
- **Sharing files you can't VACUUM safely.** Shared Delta tables can't VACUUM aggressively without breaking recipient time-travel reads.
- **Distributing very large historical snapshots.** Consider Delta Sharing of a *materialized snapshot table* sized for the consumer, not the raw history.

## Weaknesses — areas Delta Sharing does not do well

- **Egress accrues to provider.** Recipients reading TBs hit provider's cloud egress bill.
- **Revocation isn't instant.** Pre-signed URLs in flight outlive grant deletion until they expire (default ~1h).
- **CDF over sharing has caveats.** Schema changes on provider can interrupt recipient CDF reads.
- **D2O auditing is thinner than D2D.** Token-based access; provider-side logs are the source of truth.
- **Views support is limited.** Complex views (UDFs, non-Spark SQL) don't share.
- **Notebook / model sharing is D2D-only.** Open recipients can't consume them.
- **Bandwidth tuning is provider-side.** Recipient can't ask for "give me bigger files" — depends on how provider OPTIMIZE-d.
- **OPTIMIZE on provider rewrites file URLs in recipient view.** Active streaming readers may see file-not-found if URLs were cached past OPTIMIZE.
- **No first-class row/column filtering per recipient.** Provider can pre-project via shared views, but it's static, not per-recipient dynamic.
- **Recipient compute does the work.** Wide queries against a thin recipient cluster will be slow regardless of provider data layout.
- **Marketplace listings via Delta Sharing inherit all of the above.** Marketplace is the discovery layer; constraints carry through.
- **Cross-cloud sharing works but egress is brutal.** Prefer same-cloud, same-region when possible.

---

## Sources

- [Delta Sharing docs (Databricks)](https://docs.databricks.com/aws/en/data-sharing/)
- [Delta Sharing protocol (open spec)](https://github.com/delta-io/delta-sharing/blob/main/PROTOCOL.md)
- [D2D vs D2O comparison](https://docs.databricks.com/aws/en/data-sharing/share-data-databricks)
- [Sharing CDF](https://docs.databricks.com/aws/en/data-sharing/share-cdf)
- [Glossary](../Glossary/glossary.md)
