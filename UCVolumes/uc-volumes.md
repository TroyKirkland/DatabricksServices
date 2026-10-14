# UC Volumes

## Level 100 — What it is

A UC Volume is a **governed non-tabular storage abstraction** inside Unity Catalog. It gives you a path-based namespace (`/Volumes/<catalog>/<schema>/<volume>/...`) backed by cloud object storage, with full UC permissions, lineage, and audit. Think "a managed folder in the lakehouse for files that aren't tables".

- **For non-tabular data**: PDFs, images, JSON blobs, model artifacts, init scripts, config files.
- **UC-governed**: same permission model as tables (READ VOLUME, WRITE VOLUME, MANAGE VOLUME).
- **Universal access**: usable from Python `open()`, Spark, dbutils.fs, REST, CLI, and most BI/data tools that can mount a path.

## Level 200 — The product surface

- **Two types**:
  - **Managed volume** — UC owns the storage location; created with `CREATE VOLUME <name>`.
  - **External volume** — points at an existing cloud path via an external location + storage credential.
- **Path scheme**: `/Volumes/<catalog>/<schema>/<volume>/<sub-path>`. Treat as a filesystem.
- **Operations**: ls, cp, mv, rm, mkdir, read, write — via Catalog Explorer UI, `dbutils.fs`, REST `/api/2.0/fs/files`, the Databricks CLI, or normal Python file I/O on a cluster.
- **Permissions**: READ VOLUME, WRITE VOLUME, MANAGE VOLUME (plus USE CATALOG / USE SCHEMA on parents).
- **Uses**:
  - Storing input files for Auto Loader / `COPY INTO`.
  - Holding model artifacts, MLflow uploads, image datasets.
  - UC-volume init scripts for clusters (recommended over DBFS init scripts).
  - Backing files served by Databricks Apps.
  - Landing area for Lakeflow Connect SaaS connectors.

## Level 300 — Architecture

```
[Client (cluster / app / dbutils / CLI / Catalog Explorer)]
                │
                ▼
        /Volumes/<cat>/<sch>/<vol>/... path
                │
                ▼
  UC permission check (READ/WRITE VOLUME + parent USE)
                │
                ▼
  Storage credential (UC) → cloud object storage
                                (S3 / ADLS / GCS)
```

- **No special protocol**: it's an abstraction over cloud storage with UC fronting authorization.
- **Same backing store** as managed Delta tables (managed volumes live in the metastore-managed root by default).
- **Files API**: `/api/2.0/fs/files/Volumes/...` gives REST upload/download with multipart support.

## Level 400 — Deep internals

### File API and SDK access

- Files API supports GET, PUT (multipart for large), DELETE, HEAD.
- Python: `databricks-sdk` exposes `WorkspaceClient.files.upload(...)` / `download(...)`.
- CLI: `databricks fs cp file:./local/foo /Volumes/cat/sch/vol/foo`.
- Direct Spark: `spark.read.text("/Volumes/cat/sch/vol/foo.txt")`.
- Python builtins: `open("/Volumes/.../foo")` works on cluster (the FUSE-style mount handles it).

### Managed vs external volumes

- **Managed**: storage owned by UC; cleanup on `DROP VOLUME` removes data after retention. Predictive Optimization doesn't apply (it's for Delta tables), but file lifecycle is UC's.
- **External**: created against an external location; UC tracks the namespace but cloud lifecycle is on you. Multiple external volumes can point at the same physical prefix (with care).

### Permissions detail

- READ VOLUME ⟹ list, read.
- WRITE VOLUME ⟹ create, write, modify.
- MANAGE VOLUME ⟹ delete the volume, alter properties.
- Plus USE CATALOG and USE SCHEMA on the parents.
- Group/SP/user grants like any UC object.

### FUSE-style cluster mount

- On a cluster, `/Volumes/...` is mounted via a Databricks-specific FUSE driver.
- Reads stream the file lazily; writes upload on close (large files use multipart under the hood).
- `os.scandir("/Volumes/cat/sch/vol")` works; performance is fine for small/medium files, slower for many tiny files.

### Init scripts

- Cluster init scripts can be stored in a UC volume and referenced by path; this is the supported successor to DBFS init scripts.
- Permissions on the volume control who can read the init script; the cluster's run-as identity must have READ VOLUME.

### Auto Loader + Volumes

- Auto Loader can consume from `/Volumes/.../` paths.
- File-notification mode requires the underlying cloud storage to be event-source-capable (managed volumes do this); for external volumes you may need to set up subscriptions on the underlying prefix.

### Databricks Apps + Volumes

- Apps can read/write volumes via the resources mechanism in `app.yaml`.
- Lets an app serve user uploads from a governed location.

### Limits / realities

- Maximum file size: cloud-storage-dependent; multipart upload limits apply (e.g., 5 TB per S3 object).
- Listing very large directories (>1M files) is slow; partition into sub-paths.
- Cross-region access pays egress.
- Cluster-side `os.path` semantics differ subtly from a native filesystem (e.g., no symlinks).
- DBFS root storage is deprecated for new workloads; volumes are the path forward.
- Foreign tools that mount paths must handle UC auth (Databricks creds).

### Mental model

A UC Volume is **"a UC-governed folder"**. Same role as DBFS used to play, but governed, auditable, and modern. Default to managed volumes; reach for external when sharing with non-Databricks consumers.

---

## Anti-patterns — where you should not use UC Volumes

- **Storing tabular data.** Use a Delta table. Volumes don't get OPTIMIZE / time travel / lineage at row level.
- **Hot-path KV lookup.** It's object storage. Use Lakebase or an actual KV store.
- **Streaming high-rate writes of small files.** Object stores hate millions of tiny files. Batch + compact, or use a streaming sink to Delta.
- **DBFS-replacement migrations done poorly.** Don't just symlink old paths; replace references via DABs / IaC.
- **Mounting a volume to share files with arbitrary external systems without governance.** Use Delta Sharing for sharing; Volumes are for in-lakehouse non-tabular.
- **Storing secrets.** Use Databricks Secrets Manager.
- **Large model registries by file path.** Use MLflow registered models in UC.
- **Backup root for unstructured data dump.** Volumes work but lifecycle / retention isn't S3-Glacier-cheap. Pair with cloud lifecycle on external volumes if cost matters.
- **Init script "scratch space".** Volumes are auditable; don't use them to hide one-off scripts. Manage init scripts as code.
- **Per-user scratch folders without governance.** Either grant per-user via groups, or use a different abstraction.

## Weaknesses — areas UC Volumes do not do well

- **No first-class versioning.** Files are object-store-versioned (if you enable cloud versioning), but no Delta-style time travel.
- **No lineage on file reads/writes.** Audit logs the access; lineage doesn't trace file → downstream consumer.
- **Listing perf on huge prefixes.** Same as raw cloud storage; partition aggressively.
- **FUSE limitations.** Some POSIX semantics (mmap, symlinks, hard links) don't work or behave subtly differently.
- **External volume cleanup is manual.** Dropping the volume doesn't delete the data.
- **Bulk file ops via the API are paginated.** Large transfers need CLI / SDK with concurrency.
- **No SAS / pre-signed URLs by default.** For external sharing of files, you need to use the Files API or generate vendor-specific URLs.
- **Init script discovery in DBR has friction.** Path must be right and permissions exact.
- **No row/column policies.** It's files, not tables.
- **Storage-tier choice limited on managed volumes.** UC picks the tier; you don't.
- **Multi-region volumes don't exist.** A volume is in one region's metastore.
- **Per-file tagging is limited.** Tagging is at the volume level, not per-file.

---

## Sources

- [UC Volumes docs](https://docs.databricks.com/aws/en/connect/unity-catalog/volumes)
- [Files API](https://docs.databricks.com/api/workspace/files)
- [UC init scripts](https://docs.databricks.com/aws/en/init-scripts/cluster-scoped)
- [Volumes governance](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-volumes)
- [Glossary](../Glossary/glossary.md)
