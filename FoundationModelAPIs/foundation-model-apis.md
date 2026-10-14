# Foundation Model APIs (FMAPI)

## Level 100 — What it is

FMAPI is **Databricks-hosted AI models** — open-source and commercial offerings from OpenAI, Anthropic, Google, Meta, xAI, DeepSeek, Alibaba, and others — served behind Mosaic AI Model Serving with two billing modes. You get LLMs, embedding models, and reranking models directly inside your Databricks workspace; Databricks manages the billing relationship and infrastructure for the full catalog.

- **Hosted models**: OpenAI GPT-5/6 series; Anthropic Claude (Haiku, Sonnet, Fable, Opus); Google Gemini 3.x; Meta Llama 4; xAI Grok; DeepSeek V4; Alibaba Qwen3; GTE/BGE embeddings; and others.
- **Two billing modes**: pay-per-token (shared infra) and provisioned throughput (dedicated capacity).
- **Same surface as any Model Serving endpoint**: REST API, `ai_query`, MLflow deployments client.

## Level 200 — The product surface

- **Models** (subject to availability per region):
  - **OpenAI**: GPT-5 and GPT-6 families (text + image; pay-per-token).
  - **Anthropic Claude**: Haiku 4.5, Sonnet 4.x/5.x, Fable 5.x, Opus 4.x/5.x (text + image; pay-per-token).
  - **Google**: Gemini 3.x Flash/Pro (text, image, video, audio); Gemma 3 12B (text + image).
  - **Meta Llama**: Llama 4 Maverick (text + image); Llama 3.3 70B and 3.1 8B Instruct (text).
  - **xAI Grok**: Grok 4.6/4.7 (text; pay-per-token).
  - **DeepSeek V4** Flash and Pro variants (text; note: V4 Pro retiring Oct 30, 2026).
  - **Alibaba Qwen3**: 122B and 80B instruct, Qwen3-Embedding-0.6B (Public Preview).
  - **Zhipu GLM 5.x**, **Moonshot Kimi K3**: additional text/multimodal options.
  - **Embeddings**: `databricks-gte-large-en` (GTE Large En, 1024-dim), BGE Large En, Qwen3-Embedding-0.6B (Public Preview).
  - Reranking models for hybrid retrieval pipelines.
  - Note: DBRX and Mistral variants are no longer in the active catalog. Gemini 2.5 series retired October 2026.
- **Pay-per-token** — shared endpoint; charge per (input + output) token. Lowest barrier; subject to global rate limits.
- **Provisioned throughput** — reserved capacity in tokens-per-second; predictable latency, no cold start, fixed-ish cost.
- **Endpoints** — each model has a stable endpoint name (e.g., `databricks-meta-llama-3-x-instruct`).
- **Routing through Unity Gateway** (formerly Mosaic AI Gateway) for policy, observability, and smart routing.
- **Access**: workspace OAuth; UC governance on the endpoint.

## Level 300 — Architecture

```
[Caller (DBSQL ai_query / app / notebook)]
        │
        ▼
[FMAPI endpoint = Mosaic AI Model Serving endpoint]
        ├─ Pay-per-token  → multi-tenant inference pool (cold start avoided by global warm)
        └─ Provisioned    → dedicated replicas for the workspace (no cold start)
        │
        ▼
[Token meter] ──► system.serving.endpoint_usage
        │
        ▼
[Response]
```

- **Pay-per-token** runs on Databricks' multi-tenant inference pool; warm pool keeps cold-start invisible.
- **Provisioned throughput** runs dedicated replicas; you pre-commit to tokens-per-second.
- **Gateway integration** adds policy, payload tables, fallback.

## Level 400 — Deep internals

### Pay-per-token mode

- Shared infrastructure, billed per million tokens (input + output, model-specific rates).
- Global rate limits apply across all workspaces; bursts can hit ceiling.
- Cold start is hidden by Databricks-managed warm pool.
- Best for prototyping, low-volume production, or bursty workloads.

### Provisioned throughput mode

- Specify `provisioned_throughput` tokens/s when creating endpoint.
- Dedicated replicas allocated; cost is per replica-hour, not per token (within capacity).
- No cold start; latency stable.
- Best for high-volume steady-state production or strict latency SLAs.
- Capacity is configurable; up/down within capacity limits of the model class.

### Performance characteristics

- Time-to-first-token: model-dependent; small models (8B) sub-second, large models (70B+) seconds.
- Output token rate: tens to hundreds of tokens/s per replica.
- Throughput scaling: provisioned scales linearly with replica count up to model class limits.

### Model lifecycle

- Databricks introduces and retires models with notice; check release notes.
- Each model has a stable endpoint name + version suffix; older versions may be archived.
- New model rollouts may be gated by region.

### Embedding models

- `databricks-gte-large-en`: 1024-dim general-text embeddings. Used by Vector Search and Agent Bricks defaults.
- `databricks-bge-large-en`: alternative general-text embedding model.
- Qwen3-Embedding-0.6B: compact multilingual embedding model (Public Preview as of Oct 2026).
- Pay-per-token billing per input token.
- Provisioned throughput available for high-volume indexing.

### Function calling and tools

- Most chat models (Llama 3.x/4.x, OpenAI GPT-5/6, Claude, Gemini) support function/tool calling in the chat API; Databricks endpoints surface the same schema.
- Use `ai_query` with structured outputs in DBSQL or the Python client.

### Streaming

- SSE supported on chat endpoints.
- Latency benefits for interactive UX; combine with Gateway final-guardrail at end-of-stream.

### Cost attribution

- `system.serving.endpoint_usage` exposes per-endpoint token counts.
- Join with `system.billing.list_prices` for $/token.
- Payload tables (Gateway) give per-call detail.

### Limits / realities

- Pay-per-token has per-workspace and global rate limits; failures during big bursts.
- Provisioned throughput requires commit; can't undo a 24-hour reservation instantly.
- Some advanced model features (multimodal, vision) availability depends on the specific model.
- Cross-region availability: not all models in all regions.
- Token counts include both input and output; long prompts + verbose responses get expensive.
- Context window varies by model; check before relying on long-context behaviour.

### Mental model

FMAPI is **"OpenAI-style hosted LLMs, but governed inside Databricks"**. Pay-per-token = OpenAI-pricing-equivalent shared infra. Provisioned throughput = dedicated capacity for production. Treat as a building block for higher-level products (Agent Bricks, Genie, `ai_query`).

---

## Anti-patterns — where you should not use FMAPI

- **Real-time hard-latency requirements (under 100ms).** FMAPI is hundreds of ms+; for sub-100ms use specialised in-process models.
- **Pay-per-token for steady-state high-volume.** Provisioned cheaper at scale; switch when usage stabilises.
- **Provisioned throughput for bursty / unpredictable usage.** You'll pay for unused capacity. Pay-per-token is better.
- **Long-context use cases without verifying model context window.** Context window is per-model.
- **Assuming all hosted models share the same modality support.** Text+image is now available across most families; video/audio input is currently Gemini-only; image generation is model-specific. Verify capabilities on the supported-models page before designing pipelines.
- **Treating FMAPI as a model registry.** Models are managed by Databricks; you don't fine-tune them in-place (you'd register a fine-tune as a separate custom served entity).
- **Inferring at speed not capped by model class.** A 70B model is what it is; bigger compute doesn't speed up generation per token.
- **Without payload sampling on Gateway.** High-volume logging explodes storage.
- **As the only model option in a vendor-neutral architecture.** Have a fallback model (different provider) for outages.
- **For deterministic outputs.** Temperature can be 0 but model updates change behaviour subtly.

## Weaknesses — areas FMAPI does not do well

- **Model selection is curated, not exhaustive.** Some niche open-source models aren't hosted.
- **Pay-per-token rate limits surface as failures.** Need retry logic; Gateway helps.
- **Provisioned throughput commit periods.** Adjusting is non-instant.
- **No first-class fine-tuning on hosted models.** Fine-tune externally or use custom model serving.
- **Region availability gaps.** Some models / sizes only in certain regions.
- **Cost prediction tooling is OK, not great.** Estimating monthly $ requires historical usage analysis.
- **Streaming + structured outputs interaction can be tricky.** Function calling + SSE has edge cases.
- **Context window evolution.** Model upgrades can change defaults; pin client-side params.
- **Custom system prompts limited per model.** Some chat templates are fixed.
- **Multimodal coverage varies by modality.** Text+image is mainstream across most providers. Video/audio input is Gemini-specific. Image generation is limited to specific Gemini models. Audio generation and transcription not native.
- **Tokenizer mismatches.** Counts in `system.serving.endpoint_usage` may differ slightly from third-party token counters.
- **Latency variability under load.** Even provisioned can show spikes during scale-up.

---

## Sources

- [Foundation Model APIs docs](https://docs.databricks.com/aws/en/machine-learning/foundation-models/)
- [Pay-per-token](https://docs.databricks.com/aws/en/machine-learning/foundation-models/pay-per-token)
- [Provisioned throughput](https://docs.databricks.com/aws/en/machine-learning/foundation-models/provisioned-throughput)
- [Supported models](https://docs.databricks.com/aws/en/machine-learning/foundation-models/supported-models)
- [Glossary](../Glossary/glossary.md)
