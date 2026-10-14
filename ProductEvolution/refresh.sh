#!/bin/bash
# Weekly/monthly refresh: scrape release notes -> classify -> render per-product PNGs
# -> commit & push regenerated files to origin.
#
# Usage:
#   ./refresh.sh                          # last 12 months ending current month; commits + pushes
#   ./refresh.sh 2025-01 2026-05          # explicit range
#   ./refresh.sh --local-only             # skip git commit + push (preview only)
#   ./refresh.sh 2025-01 2026-05 --local-only
#
# Reads ANTHROPIC_API_KEY from env (or your shell rc). If unset, falls back to
# keyword classifier and tells you. Set via:
#   export ANTHROPIC_API_KEY=sk-ant-...
#
# Requires: python3, curl (built-in on macOS), rsvg-convert (brew install librsvg)
# For commit+push: git + a configured origin remote.

set -euo pipefail
cd "$(dirname "$0")"

# ── Arg parsing ────────────────────────────────────────────────
LOCAL_ONLY=0
POS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --local-only|--no-push) LOCAL_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do POS+=("$1"); shift; done ;;
    *)  POS+=("$1"); shift ;;
  esac
done

# ── Date range ─────────────────────────────────────────────────
if [ ${#POS[@]} -eq 2 ]; then
  START="${POS[0]}"
  END="${POS[1]}"
else
  # Default: last 12 months ending this month.
  END=$(date "+%Y-%m")
  START=$(python3 -c "
from datetime import date, timedelta
d = date.today().replace(day=1)
for _ in range(12):
    d = (d - timedelta(days=1)).replace(day=1)
print(d.strftime('%Y-%m'))
")
fi

echo "== Databricks release-notes refresh =="
echo "  range:        $START .. $END"
echo "  refresh date: $(date '+%Y-%m-%d')"
echo "  api key:      $([ -n "${ANTHROPIC_API_KEY:-}" ] && echo set || echo NOT SET — will use keyword fallback)"
echo "  mode:         $([ "$LOCAL_ONLY" = "1" ] && echo "local-only (no commit/push)" || echo "auto commit + push")"
echo

RAW_JSON="features-12mo.json"
CLASSIFIED_JSON="features-classified.json"

# ── Snapshot prior run for diff ────────────────────────────────
PRIOR_COUNT=0
if [ -f "$CLASSIFIED_JSON" ]; then
  PRIOR_COUNT=$(python3 -c "import json; print(len(json.load(open('$CLASSIFIED_JSON'))))")
fi

# ── 1. Scrape ──────────────────────────────────────────────────
echo "[1/4] Scrape"
python3 feature-scraper.py "$START" "$END" -o "$RAW_JSON"
NEW_COUNT=$(python3 -c "import json; print(len(json.load(open('$RAW_JSON'))))")
echo

# ── 2. Classify ────────────────────────────────────────────────
echo "[2/4] Classify"
python3 feature-classifier.py "$RAW_JSON" -o "$CLASSIFIED_JSON"
echo

# ── 3. Render per-product timelines ────────────────────────────
echo "[3/4] Render"
python3 feature-timeline.py "$CLASSIFIED_JSON" --render-png

# ── 4. Commit + push ───────────────────────────────────────────
if [ "$LOCAL_ONLY" = "1" ]; then
  echo
  echo "[4/4] Skipping git (--local-only)"
else
  echo
  echo "[4/4] Commit + push"

  # Confirm we're in a git repo with an origin remote.
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "  not a git repo — skipping commit/push. Run from a clone of the repo, or use --local-only."
  elif ! git remote get-url origin >/dev/null 2>&1; then
    echo "  no 'origin' remote configured — skipping commit/push."
  else
    # Stage ONLY the regenerated files (not your other in-flight edits).
    PATHS=(features-12mo.json features-classified.json by-product/)
    git add -- "${PATHS[@]}" 2>/dev/null || true

    if git diff --cached --quiet -- "${PATHS[@]}"; then
      echo "  no changes to feature-timeline outputs — nothing to commit."
    else
      echo "  staged:"
      git status --short -- "${PATHS[@]}" | sed 's/^/    /'

      CLASSIFIER=$([ -n "${ANTHROPIC_API_KEY:-}" ] && echo "LLM (Haiku)" || echo "keyword fallback")
      git commit -m "$(cat <<EOF
Refresh feature timelines: ${START} → ${END}

events: ${PRIOR_COUNT} → ${NEW_COUNT}  (Δ $((NEW_COUNT - PRIOR_COUNT)))
classifier: ${CLASSIFIER}
generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

via ProductEvolution/refresh.sh
EOF
)"
      echo
      echo "  pushing to origin..."
      git push origin HEAD
    fi
  fi
fi

# ── Summary ────────────────────────────────────────────────────
echo
echo "== Done =="
echo "  events: $PRIOR_COUNT → $NEW_COUNT  (Δ $((NEW_COUNT - PRIOR_COUNT)))"
echo "  outputs: by-product/*.png  ·  $CLASSIFIED_JSON"
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo
  echo "  NOTE: ANTHROPIC_API_KEY was unset. For the better LLM classification, run:"
  echo "    export ANTHROPIC_API_KEY=sk-ant-..."
  echo "    ./refresh.sh"
fi
