#!/usr/bin/env python3
"""Classify release-notes events into product buckets.

Primary path: Anthropic API (Haiku 4.5) — batched, JSON-mode.
Fallback:     keyword scan — runs automatically if ANTHROPIC_API_KEY is unset.

Input:  features-12mo.json (output of feature-scraper.py)
Output: features-classified.json (same fields + "bucket" per event)

Usage:
    python3 feature-classifier.py features-12mo.json -o features-classified.json
    python3 feature-classifier.py features-12mo.json --no-llm        # keyword path only
    python3 feature-classifier.py features-12mo.json --model claude-sonnet-4-6
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

# ============================================================
# Taxonomy — source of truth.
# To add a bucket: append here AND add a palette entry in feature-timeline.py.
# ============================================================
TAXONOMY = {
    "lakeflow": {
        "title": "Lakeflow",
        "hint": "Lakeflow Jobs (was Workflows), Lakeflow Spark Declarative Pipelines (was DLT), "
                "Lakeflow Connect (ingestion incl. Arcion CDC), Auto Loader, Zerobus Ingest.",
        "keywords": [
            "lakeflow", "declarative pipeline", "spark declarative", "delta live table", "dlt",
            "workflow", "job task", "auto loader", "cloudfiles", "zerobus",
            "apply changes", "ingestion connector", "managed ingestion",
        ],
    },
    "unity-catalog": {
        "title": "Unity Catalog",
        "hint": "Unity Catalog governance, UC Volumes, Clean Rooms, Delta Sharing, system tables, "
                "row filters / column masks, ABAC, lineage.",
        "keywords": [
            "unity catalog", "catalog commit", "uc volume", "external location", "storage credential",
            "row filter", "column mask", "lineage", "clean room", "delta sharing",
            "recipient", "abac", "system table", "audit log", "governance",
        ],
    },
    "delta-lake": {
        "title": "Delta Lake",
        "hint": "Delta format features: liquid clustering, predictive optimization, deletion vectors, "
                "UniForm, CDF, time travel, column mapping, OPTIMIZE/VACUUM.",
        "keywords": [
            "delta lake", "liquid clustering", "predictive optimization", "deletion vector",
            "uniform", "change data feed", "time travel", "column mapping", "type widening",
            "delta table", "optimize", "vacuum", "z-order", "partition metadata",
        ],
    },
    "databricks-sql": {
        "title": "Databricks SQL",
        "hint": "SQL Warehouses, SQL language features (ai_query, ai_classify, materialized views, "
                "pipe syntax), alerts, Statement Execution API, query history.",
        "keywords": [
            "sql warehouse", "sql alert", "sql endpoint", "statement execution", "query history",
            "dbsql", "databricks sql", "sql editor", "sql function", "ai_query", "ai_classify",
            "materialized view", "sql scripting", "stored procedure", "pipe syntax",
        ],
    },
    "aibi": {
        "title": "AI/BI (Dashboards & Genie)",
        "hint": "AI/BI Dashboards (was Lakeview), AI/BI Genie (was Lakehouse IQ) NL-to-SQL.",
        "keywords": [
            "ai/bi", "aibi", "dashboard", "genie", "genie space", "lakeview", "lakehouse iq",
        ],
    },
    "mosaic-ai": {
        "title": "Mosaic AI",
        "hint": "Model Serving, Vector Search, Foundation Model APIs, Agent Bricks, AI Gateway, "
                "MLflow, Feature Store/Engineering, AI Playground, Assistant, RAG, embeddings.",
        "keywords": [
            "mosaic ai", "model serving", "foundation model", "vector search", "agent brick",
            "knowledge assistant", "multi-agent supervisor", "ai gateway", "mlflow",
            "feature engineering", "feature store", "online table", "llm", "gpt", "claude",
            "llama", "dbrx", "qwen", "playground", "assistant", "inference table", "rag",
            "embedding", "ai_parse_document",
        ],
    },
    "lakebase": {
        "title": "Lakebase",
        "hint": "Managed Postgres on Databricks: provisioned, autoscale, branching, scale-to-zero.",
        "keywords": ["lakebase", "postgres", "autoscale", "branching", "lakehouse sync"],
    },
    "compute": {
        "title": "Compute & Runtime",
        "hint": "Databricks Runtime, Photon, Spark, serverless general compute, Databricks Connect, "
                "cluster pools, init scripts, access modes, instance types.",
        "keywords": [
            "databricks runtime", "dbr", "photon", "serverless compute", "serverless notebook",
            "serverless general", "cluster pool", "init script", "access mode", "dedicated",
            "standard access", "single user", "spark connect", "databricks connect",
            "compute policy", "instance type", "gpu",
        ],
    },
    "apps-ecosystem": {
        "title": "Apps & Ecosystem",
        "hint": "Databricks Apps (Streamlit/Dash/FastAPI hosting), Databricks Asset Bundles (DAB), "
                "Marketplace, listings.",
        "keywords": [
            "databricks app", "app telemetry", "asset bundle", "dab", "marketplace", "listing",
        ],
    },
    "workspace": {
        "title": "Workspace & Notebooks",
        "hint": "Notebook UI, workspace files, Git folders (was Repos), Excel/Google Sheets add-ins, "
                "unified runs list, scheduled refresh, operational emails.",
        "keywords": [
            "notebook", "workspace file", "git folder", "repo", "run history", "scheduled refresh",
            "excel add-in", "google sheets", "workspace setting", "operational email", "unified runs",
        ],
    },
    "security": {
        "title": "Security & Networking",
        "hint": "Auth (OAuth, SAML, Entra ID, Okta), PrivateLink, NCC, audit, identity management, "
                "compliance profiles, network policies, token federation, PAT management.",
        "keywords": [
            "oauth", "saml", "entra id", "okta", "identity management", "service principal",
            "personal access token", "privatelink", "network connectivity", "network policy",
            "compliance security profile", "hipaa", "audit", "token federation", "egress",
        ],
    },
    "other": {
        "title": "Other",
        "hint": "Catch-all for events that don't fit a primary bucket.",
        "keywords": [],
    },
}

ALLOWED_BUCKETS = list(TAXONOMY.keys())

# ============================================================
# Keyword fallback
# ============================================================
def classify_keywords(title: str) -> str:
    """Return the slug of the first bucket whose keywords match the title."""
    t = title.lower()
    for slug, info in TAXONOMY.items():
        if slug == "other":
            continue
        for kw in info["keywords"]:
            if kw in t:
                return slug
    return "other"

# ============================================================
# LLM classifier (Anthropic API via curl-style stdlib request)
# ============================================================
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

def _build_prompt(events: list[dict]) -> str:
    bucket_lines = "\n".join(
        f"- {slug}: {TAXONOMY[slug]['hint']}"
        for slug in ALLOWED_BUCKETS if slug != "other"
    )
    bucket_lines += "\n- other: only if no other bucket fits"
    entries = "\n".join(f'  {{"slug": "{e["slug"]}", "title": {json.dumps(e["title"])}}}'
                        for e in events)
    return (
        "You are classifying Databricks release-notes entries into product buckets.\n\n"
        f"Buckets:\n{bucket_lines}\n\n"
        "For each entry, choose the SINGLE best bucket. Return ONLY a JSON array, no prose:\n"
        '[{"slug": "...", "bucket": "..."}, ...]\n\n'
        f"Entries:\n[\n{entries}\n]\n"
    )

def _call_anthropic(api_key: str, model: str, prompt: str, max_tokens: int = 4096) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode("utf-8"))
    # Concatenate any text blocks
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")

def classify_llm(events: list[dict], api_key: str, model: str, batch_size: int = 25) -> dict[str, str]:
    """Returns {slug: bucket}. Batches to avoid huge prompts."""
    out = {}
    n = len(events)
    for i in range(0, n, batch_size):
        chunk = events[i : i + batch_size]
        prompt = _build_prompt(chunk)
        for attempt in range(3):
            try:
                raw = _call_anthropic(api_key, model, prompt)
                # Be lenient: pull the first JSON array out
                m = re.search(r"\[\s*\{.*?\}\s*\]", raw, re.DOTALL)
                if not m:
                    raise ValueError(f"no JSON array in response: {raw[:200]!r}")
                arr = json.loads(m.group(0))
                for item in arr:
                    slug = item.get("slug")
                    bucket = item.get("bucket")
                    if slug and bucket in ALLOWED_BUCKETS:
                        out[slug] = bucket
                print(f"  batch {i // batch_size + 1}/{(n + batch_size - 1) // batch_size}: "
                      f"{len(chunk)} events → {sum(1 for c in chunk if c['slug'] in out)} classified",
                      file=sys.stderr)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
                print(f"  batch {i // batch_size + 1} attempt {attempt + 1} error: {e}",
                      file=sys.stderr)
                time.sleep(2 ** attempt)
        else:
            print(f"  batch {i // batch_size + 1} FAILED after 3 attempts — falling back to keywords",
                  file=sys.stderr)
            for e in chunk:
                out[e["slug"]] = classify_keywords(e["title"])
    return out

# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="features JSON from feature-scraper.py")
    ap.add_argument("-o", "--out", default="features-classified.json")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip LLM, use keyword fallback only")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001",
                    help="Anthropic model id (default: Haiku 4.5)")
    args = ap.parse_args()

    events = json.load(open(args.input))
    print(f"loaded {len(events)} events from {args.input}", file=sys.stderr)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.no_llm or not api_key:
        if not api_key and not args.no_llm:
            print("ANTHROPIC_API_KEY not set — using keyword fallback. "
                  "Set the env var to use the LLM path.", file=sys.stderr)
        for e in events:
            e["bucket"] = classify_keywords(e["title"])
        method = "keyword"
    else:
        print(f"using LLM model={args.model}", file=sys.stderr)
        mapping = classify_llm(events, api_key, args.model)
        for e in events:
            e["bucket"] = mapping.get(e["slug"]) or classify_keywords(e["title"])
        method = f"llm({args.model})"

    from collections import Counter
    dist = Counter(e["bucket"] for e in events)
    print(f"\nclassification ({method}):", file=sys.stderr)
    for b, n in dist.most_common():
        print(f"  {b:18s} {n:3d}", file=sys.stderr)

    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)
    print(f"\n→ {args.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
