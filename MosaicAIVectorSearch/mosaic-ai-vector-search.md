# Mosaic AI Vector Search

## Level 100 — What it is

Mosaic AI Vector Search is **Databricks' vector database**. You define a vector index over a UC column (text or pre-computed embeddings); Databricks keeps it in sync with the source Delta table; you query by similarity at low latency.

- **UC-native**: indexes are UC objects, governed and lineage-tracked.
- **Delta-synced**: keep embeddings up to date as the source Delta table changes.
- **Hybrid search**: combine vector similarity with metadata filters and (optionally) BM25 keyword scoring.

## Level 200 — The product surface

- **Vector Search endpoint** — the compute serving indexes. Two flavors:
  - **Storage-optimized** — cheap, slower queries; good for large infrequent-query indexes.
  - **Standard** — faster queries, higher cost; for production lookups.
- **Index** — a UC object over a source Delta table. Three index types:
  - **Delta Sync Index** — points at a Delta table; Databricks computes embeddings (calls a configured embedding model endpoint) and keeps the index in sync.
  - **Direct Vector Access Index** — you bring your own embeddings and write them via the SDK.
  - **Full-Text Search Index** (Beta, storage-optimized endpoints only) — keyword-based BM25 search on text columns without requiring vector embeddings; uses triggered sync only.
- **Embeddings** — by Databricks (FMAPI embedding models like `databricks-gte-large-en`) or by you (e.g., OpenAI, Cohere). Embedding endpoint can be any Model Serving endpoint that returns vectors.
- **Query API** — `similarity_search(query_text or vector, k, filters)` returns top-k with scores + selected metadata columns.
- **Filters** — boolean SQL-like expressions over the index's metadata columns: `category="cars" AND price<1000`.
- **Hybrid** — query both vector + keyword in one call; results merged using Reciprocal Rank Fusion (RRF). Hybrid queries capped at 200 results; pure ANN queries up to 10,000.
- **SDK / REST**: Python (`databricks-vectorsearch`), REST API, supported in LangChain/LangGraph/DSPy.
- **Performance tip**: service principals deliver up to 100 ms faster per-query latency than personal access tokens in production.

## Level 300 — Architecture

```
[Source Delta table]
  cols: id, text, metadata1, metadata2, ...
        │
        ▼
[Embedding model endpoint]  (Databricks-hosted or external)
        │
        ▼
[Vector Search index (UC obj)]  ◄── kept in sync by Delta Sync (CDF based)
        │
        ▼
[Vector Search endpoint compute]
        ▲
        │
Query: similarity_search(query, k, filters)
        │
[Caller (agent / app / notebook / DBSQL via ai_query)]
```

- **Sync pipeline**: Delta Sync indexes listen to CDF / table version changes, recompute embeddings for added/changed rows, update the index.
- **Indexing structure**: ANN (HNSW-style) on the vectors; filters evaluated alongside.
- **Endpoint compute**: Databricks-managed; standard endpoints scale on QPS.

## Level 400 — Deep internals

### Index types — details

- **Delta Sync Index, Compute Embeddings**: Databricks reads source rows, calls the embedding endpoint, stores vector + metadata in the index. Best when you don't want to compute embeddings yourself.
- **Delta Sync Index, Self-Managed Embeddings**: source table already has an embedding column; Databricks just syncs.
- **Direct Vector Access Index**: not tied to a Delta table; write via SDK. Useful for streaming embeddings from outside Databricks.
- **Full-Text Search Index** (Beta): pure keyword BM25 index on text columns — no embedding model required. Only on storage-optimized endpoints; triggered sync only. Good for exact/partial term matching where you don't want embedding cost.

### Sync semantics

- Triggered sync runs on schedule or on demand.
- Continuous sync watches the source table's CDF; updates roll within minutes.
- Backfill on first creation: full table scan + embed.
- Schema changes on the source: new metadata columns require index update; embedding column changes require recreate.

### Embeddings + embedding model

- Embedding model is a Model Serving endpoint; cost = per token of input.
- For Delta Sync with Compute, batch size is auto-managed; respects endpoint rate limits.
- Vector dimensionality fixed at index creation (e.g., 1024 for gte-large-en); can't change without recreate.

### Query path

- Client calls `similarity_search(...)`.
- Endpoint resolves index, applies metadata filters, runs ANN.
- Returns top-k with score, ID, and selected metadata columns (up to 10,000 results for ANN).
- Hybrid: runs both vector and keyword search, blends scores using Reciprocal Rank Fusion (RRF); result set capped at 200.

### Filters

- Filter expressions push to the index pre-ranking.
- Supported operators: `=`, `<`, `>`, `IN`, `AND`, `OR`, `NOT`.
- Filterable columns must be declared at index creation (metadata fields).
- Heavy filters that exclude most rows → may degrade recall if ANN-only; consider increasing `k` or using a multi-stage approach.

### Permissions and identity

- Index permissions in UC: SELECT for query, MANAGE for admin.
- Endpoint identity: the caller is checked against the index ACL; the endpoint itself uses service credentials to access source data.

### Performance characteristics

- **Standard endpoint**: tens of ms p50 for small-to-medium indexes; scales up to ~320M vectors at 768 dimensions.
- **Storage-optimized**: handles 1B+ vectors at 768 dimensions; 10–20× faster indexing than standard, but ~250 ms additional query latency. Does not support continuous sync.
- **Throughput**: scales with endpoint replicas; concurrency limits per endpoint.
- **Cold start**: standard endpoints are typically warm.
- **Auth tip**: service principals provide up to 100 ms faster per-query performance compared to personal access tokens; use service principals in production.

### Cost model

- Endpoint compute (per running hour, standard vs storage-optimized).
- Embedding compute (per source row × embedding model cost).
- Storage scales with vector count.

### Limits / realities

- One embedding model per index.
- Vector dim fixed at create; max embedding dimension is 4096.
- Source table must be a Delta table on UC (for sync indexes).
- Filter cardinality has practical limits; very high-cardinality filters can hurt latency.
- For large indexes: standard endpoints top out around 320M vectors; storage-optimized handles 1B+.
- Multiple indexes per endpoint: up to 50 indexes per endpoint.
- Hybrid search result cap: 200 results (ANN queries can return up to 10,000).
- Storage-optimized endpoints do not support continuous sync mode.

### Mental model

Vector Search is **"semantic indexes on UC tables, kept in sync, with metadata filters and hybrid scoring"**. The differentiator vs standalone vector DBs is the UC + Delta integration: your data + embeddings + access control + lineage in one place.

---

## Anti-patterns — where you should not use Vector Search

- **Tiny indexes (a few hundred vectors).** A pandas + cosine similarity check is faster and cheaper.
- **As primary storage of source documents.** Store source content in Delta / UC Volumes; the index references it by ID.
- **Where exact keyword search is needed.** Use full-text search or DBSQL `LIKE`/regex; hybrid helps but isn't a replacement for exact.
- **Frequent dimensionality changes.** Re-creating the index is the only path.
- **Sub-millisecond latency requirements.** Even Standard endpoints have ms-class latency, not μs.
- **External vector store you'd prefer to keep.** No deep migration tooling.
- **As a database for general key-value lookups.** Use Lakebase.
- **Bringing your own GPU embedding model and ignoring Model Serving.** If you want low-latency embeddings, host them in Model Serving.
- **Continuous sync on a high-churn dimension table.** You'll burn embedding budget on rows that get re-written.
- **Multi-tenant single-index across tenants.** Use one index per tenant or strong metadata filtering; mixing tenants in one index is a governance footgun.

## Weaknesses — areas Vector Search does not do well

- **Recreate on schema/dimension change.** No incremental migration.
- **Embedding cost dominates large indexes.** Pre-aggregate at index time; cache embeddings.
- **Filter combinatorics affect performance.** ANN+filter has selectivity-recall trade-offs.
- **Hybrid scoring uses RRF (not configurable per-query).** Reciprocal Rank Fusion blends vector and keyword results; the merge formula isn't tunable per-call. Hybrid results also capped at 200, which can limit recall for broad queries.
- **Limited reranking.** Bring your own reranker (LLM-as-reranker or cross-encoder) — not native.
- **No bring-your-own ANN index format.** Internal index format only.
- **Per-endpoint concurrency caps.** Need multiple endpoints for very high QPS.
- **Storage-optimized latency.** Cheap but slow; pick wisely.
- **No native distance-metric switch.** Cosine is default; some indexes expose dot-product or L2 but check the docs.
- **Sync lag visibility is limited.** Hard to see exact "this row was indexed at T".
- **Filter columns declared at create.** Adding a filterable column later requires recreate or alter (limited).
- **Cross-region not native.** One index lives in one region.

---

## Sources

- [Vector Search docs](https://docs.databricks.com/en/generative-ai/vector-search) — as of September 14, 2026
- [Index types and sync](https://docs.databricks.com/aws/en/generative-ai/create-query-vector-search)
- [Storage-optimized endpoints](https://docs.databricks.com/aws/en/generative-ai/vector-search-storage-optimized)
- [Hybrid search](https://docs.databricks.com/aws/en/generative-ai/vector-search-hybrid)
- [Glossary](../Glossary/glossary.md)
