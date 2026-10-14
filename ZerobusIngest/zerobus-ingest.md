# Zerobus Ingest

## Level 100 — What it is

Zerobus is a **gRPC SDK** that writes events directly into a Delta table in Unity Catalog, without a Kafka / Kinesis hop in between. You generate a Protobuf schema from your UC table, embed the Zerobus client into your producer app, and `send()` events at near-real-time latency.

- **Direct-to-Delta**: no broker, no message bus.
- **Low latency**: sub-second p99 in the right configuration.
- **Multi-language**: Python, Java, Go, TypeScript, Rust SDKs.

## Level 200 — The product surface

- **Producer SDK** in Python/Java/Go/TS/Rust. You instantiate a `ZerobusClient` against a UC table and call `send(event)` or `send_batch(events)`.
- **Protobuf schema** generated from a UC table definition; column types map to protobuf field types.
- **ACK semantics** — each `send` returns a future / promise that completes once Databricks has durably accepted the event. You can choose fire-and-forget or wait-on-ack.
- **At-least-once delivery** by default; idempotent producers possible via client-side dedup keys.
- **UC governance** — the target table is a regular UC managed Delta table; Zerobus is just the writer.
- **Schema evolution** — adding columns to the UC table regenerates the proto; producers compile against the new schema.
- **Auth** — service principal OAuth M2M or workspace PAT.

## Level 300 — Architecture

```
Your app
   │   (Zerobus SDK, gRPC)
   ▼
Databricks Zerobus endpoint ──► [batching, compression, dedupe] ──► Delta table (UC managed)
                                                                     │
                                                                     └── predictive optimization, OPTIMIZE/VACUUM (UC)
```

- Producer calls go over **gRPC with HTTP/2 multiplexing**; long-lived connection.
- Server-side, Zerobus batches incoming events, compresses them, and commits to Delta in micro-batches optimised for write throughput.
- ACK returns when the event is in the Delta transaction log — durable.
- The target Delta table is a normal table — Photon, OPTIMIZE, time-travel, Delta Sharing, all apply downstream.

## Level 400 — Deep internals

### Wire protocol

- gRPC, schema-driven. Each table has a dedicated endpoint URL `https://<workspace>/api/2.0/zerobus/...`.
- The Protobuf message type is generated from the UC table schema. Field numbers map to columns; new columns get new field numbers.
- Client supports streaming RPCs: `send` is a client-streaming RPC that yields ACKs back as a server-streaming response.

### ACK semantics + ordering

- ACK guarantees durability in Delta. Once ACK is received, the event survives a Databricks restart.
- Per-stream ordering is preserved (events on the same RPC stream commit in the order received).
- Across streams, ordering is best-effort; for strict ordering, use a single stream with `await ack` between sends (limits throughput).

### Throughput and batching

- Throughput peaks when sends are pipelined (do not await between sends) and the SDK auto-batches under the hood.
- Server-side commits happen on a time- or size-bound trigger; latency floor is on the order of a second in default tuning but can be pushed lower.
- For huge fan-in, partition logically (multiple producers, multiple tables, or a partitioned single table) — there is a per-table commit throughput ceiling.

### At-least-once vs exactly-once

- Default is at-least-once. If your producer retries after a network blip, you can write the same event twice.
- To make it effectively exactly-once: include a client-side dedup key column and run a downstream LDP `APPLY CHANGES INTO` (treating it as an upsert) — or accept dup-tolerant downstream consumers.

### Schema evolution

- UC `ALTER TABLE ADD COLUMN` is reflected via a new proto field number. Old producers continue to work (default values for new fields). Drop column / rename: re-generate proto, redeploy producers.
- Type narrowing or rename requires coordinated producer/consumer redeploy.

### Networking

- gRPC over TLS on standard Databricks endpoints. Private Link supported.
- For high-volume producers on a different cloud, expect WAN egress costs.

### Failure modes

- Backpressure: server can throttle if downstream commits are saturated; SDK surfaces as send latency growth and eventual error.
- Reconnect: clients reconnect with exponential backoff; resume from last-ACKed offset on the client side.
- Cluster restart on the Databricks side does not lose ACKed data (durability is Delta-backed).

### Limits / realities

- Maximum event size is bounded (typically MBs, not arbitrary GBs).
- Each table sustains a finite write throughput; for bursty multi-million-eps fan-in, shard across multiple tables.
- Schema must be representable in Protobuf. Some Delta types (e.g., complex nested arrays of maps) need careful proto design.
- Zerobus is for **writes** only. Reads are normal Delta reads.

### Mental model

Zerobus is **"Kafka producer → Delta"** with the broker removed. Equivalent to running a Kafka topic + Spark Structured Streaming sink + foreachBatch into Delta, except all the broker infrastructure is replaced by a gRPC stub and Databricks-managed commit logic. Best for "high-volume event capture into the lakehouse where you don't need consumer fan-out from a broker."

---

## Anti-patterns — where you should not use Zerobus

- **You need broker fan-out semantics.** Multiple independent consumers reading the same event stream → use Kafka/Kinesis, not Zerobus. Zerobus delivers once to Delta; downstream reads are query-based.
- **Sub-millisecond latency.** Even Zerobus has 100s of ms floor end-to-end. Use in-process buffers or real-time stream engines for sub-ms.
- **Sources you do not own.** Zerobus requires SDK in your producer. For third-party SaaS, use Lakeflow Connect or push to Kafka first.
- **Tiny event volumes.** A nightly batch of 10K rows? Use `COPY INTO` or a notebook task.
- **You want long-term replay from a topic.** Delta is durable but isn't a Kafka log replay. If consumers need historical replay, build a CDF reader downstream.
- **Strict global ordering across distributed producers.** Zerobus orders per-stream. For total ordering, you need a single producer (often impractical).
- **Heavy in-flight transformations.** Zerobus is a writer; transform downstream in LDP.
- **High-volume producers on a different cloud region.** WAN cost will eat you.
- **Use cases that require ack-on-consumed.** ACK is on-Delta-commit; if you need "ACK after downstream pipeline X processed it", build that signal yourself.
- **Mobile / browser clients.** Zerobus is for backend / service-side producers. Don't ship the SDK to end-user devices.

## Weaknesses — areas Zerobus does not do well

- **No consumer pull semantics.** Once data lands in Delta, that's it — there's no offset cursor for consumers like Kafka has.
- **Per-table commit throughput ceiling.** High-fan-in needs sharding across tables.
- **Schema evolution requires producer redeploys for non-additive changes.** Renames, type narrowings, dropped fields cascade.
- **Limited replay tooling.** No native "replay from offset X" — you query Delta CDF or use time travel.
- **At-least-once requires client dedup discipline.** No native exactly-once across retries.
- **Backpressure is opaque.** Latency grows; the SDK doesn't always give you fine-grained "I'm being throttled because Y" signals.
- **Less ecosystem than Kafka.** No Connect-equivalent, no kSQL, no schema registry beyond what Databricks provides.
- **Cross-cloud / cross-region producers cost.** WAN egress and added latency.
- **Tied to UC managed Delta.** No writing to non-Delta sinks.
- **SDK maturity varies by language.** Python and Java are most mature; TS/Go/Rust come along behind.
- **Observability is via the destination table.** Producer-side metrics (send rate, ACK latency) are client-side; you must export them to your own observability stack.
- **No native partitioning of streams beyond what you encode into the table partition column.** Partition on a sensible column or face skew.

---

## Sources

- [Zerobus Ingest docs](https://docs.databricks.com/aws/en/ingestion/zerobus/)
- [Zerobus SDK reference](https://docs.databricks.com/aws/en/ingestion/zerobus/sdk)
- [Zerobus blog](https://www.databricks.com/blog/announcing-zerobus)
- [Glossary](../Glossary/glossary.md)
