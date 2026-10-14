#!/usr/bin/env python3
"""Generate one SVG timeline per product bucket from classified release-notes events.

Input:  features-classified.json (output of feature-classifier.py)
Output: by-product/{bucket-slug}.svg  (one per bucket with >= --min-events)
        by-product/{bucket-slug}.png  (if --render-png and rsvg-convert is available)

Each SVG: a month-axis horizontal timeline with status-colored event pills stacked
within each month.

Usage:
    python3 feature-timeline.py features-classified.json
    python3 feature-timeline.py features-classified.json --min-events 5 --render-png
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

MONTHS_FULL = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]
MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Status palette: stroke, light-fill, dark-text
STATUS_PALETTE = {
    "GA":              ("#166534", "#E1EFE5"),
    "Public Preview":  ("#B45309", "#FAEFE0"),
    "Private Preview": ("#9A3412", "#FDE8E0"),
    "Beta":            ("#1E3A8A", "#E6EAF3"),
    "Update":          ("#475569", "#E7EAF0"),
}

# Bucket display titles (must match TAXONOMY in feature-classifier.py).
BUCKET_TITLES = {
    "lakeflow":       "Lakeflow",
    "unity-catalog":  "Unity Catalog",
    "delta-lake":     "Delta Lake",
    "databricks-sql": "Databricks SQL",
    "aibi":           "AI/BI (Dashboards & Genie)",
    "mosaic-ai":      "Mosaic AI",
    "lakebase":       "Lakebase",
    "compute":        "Compute & Runtime",
    "apps-ecosystem": "Apps & Ecosystem",
    "workspace":      "Workspace & Notebooks",
    "security":       "Security & Networking",
    "other":          "Other",
}

# ============================================================
# Helpers
# ============================================================
def parse_date(s: str) -> datetime:
    """Parse 'May 8, 2026' -> datetime."""
    return datetime.strptime(s, "%B %d, %Y")

def month_index(d: datetime) -> tuple[int, int]:
    return (d.year, d.month)

def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    y, m = start
    while (y, m) <= end:
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out

def truncate(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"

def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

# ============================================================
# SVG builder
# ============================================================
def build_svg(bucket: str, events: list[dict]) -> str:
    """Build SVG. Header stamp uses the latest event date in the bucket so
    output is deterministic per-input (no diff when no new events arrived)."""
    if not events:
        return ""

    # Sort and figure out time range
    for e in events:
        e["_dt"] = parse_date(e["date"])
    events.sort(key=lambda e: e["_dt"])
    data_through = events[-1]["_dt"].strftime("%Y-%m-%d")
    months = month_range(month_index(events[0]["_dt"]), month_index(events[-1]["_dt"]))
    n_months = len(months)

    # Group events by month
    by_month: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for e in events:
        by_month[month_index(e["_dt"])].append(e)
    # Stack each month bottom-to-top by date within the month (latest at top of stack)
    for mi, evs in by_month.items():
        evs.sort(key=lambda e: e["_dt"], reverse=True)

    max_stack = max(len(v) for v in by_month.values())

    # ── Layout constants ─────────────────────────────────────
    W = 2400
    HEADER_H = 80
    AXIS_H = 50
    PILL_H = 32
    PILL_GAP = 4
    STACK_TOP_PAD = 14
    STACK_BOTTOM_PAD = 14
    LEGEND_H = 80
    FOOTER_H = 30

    EVENTS_H = STACK_TOP_PAD + max_stack * (PILL_H + PILL_GAP) + STACK_BOTTOM_PAD
    H = HEADER_H + AXIS_H + EVENTS_H + LEGEND_H + FOOTER_H

    LEFT_MARGIN = 60
    RIGHT_MARGIN = 60
    col_w = (W - LEFT_MARGIN - RIGHT_MARGIN) / n_months
    pill_w = min(col_w - 16, 320)
    pill_text_chars = max(18, int(pill_w / 6.6))

    # ── Build SVG ────────────────────────────────────────────
    out = []
    out.append(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">'
    )
    out.append('''
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FBFCFD"/>
      <stop offset="1" stop-color="#EEF1F5"/>
    </linearGradient>
    <linearGradient id="headerBg" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#0B1E36"/>
      <stop offset="1" stop-color="#1F3A5F"/>
    </linearGradient>
  </defs>
''')
    out.append(f'  <rect width="{W}" height="{H}" fill="url(#bg)"/>')

    # Header
    title = BUCKET_TITLES.get(bucket, bucket)
    date_lo = events[0]["_dt"].strftime("%b %Y")
    date_hi = events[-1]["_dt"].strftime("%b %Y")
    out.append(f'  <rect x="0" y="0" width="{W}" height="{HEADER_H}" fill="url(#headerBg)"/>')
    out.append(
        f'  <text x="40" y="44" fill="#FFFFFF" font-size="28" font-weight="700">'
        f'{xml_escape(title)} <tspan fill="#9CB0CC" font-size="18" font-weight="400">'
        f'— feature timeline</tspan></text>'
    )
    out.append(
        f'  <text x="40" y="68" fill="#9CB0CC" font-size="13">'
        f'{len(events)} events  ·  {date_lo} → {date_hi}  ·  data through {data_through}'
        f'</text>'
    )
    out.append(
        f'  <rect x="{W - 130}" y="22" width="92" height="28" fill="#FF3621" rx="4"/>'
        f'<text x="{W - 84}" y="41" fill="#FFFFFF" font-size="13" font-weight="700" '
        f'text-anchor="middle">{xml_escape(bucket)}</text>'
    )

    # Month axis
    axis_y = HEADER_H + AXIS_H - 6
    out.append('  <g font-size="14" font-weight="600" fill="#0B1E36" text-anchor="middle">')
    for i, (y, m) in enumerate(months):
        cx = LEFT_MARGIN + (i + 0.5) * col_w
        label = f"{MONTHS_ABBR[m - 1]} {y % 100:02d}"
        out.append(f'    <text x="{cx:.1f}" y="{axis_y}">{label}</text>')
    out.append('  </g>')

    # Month column dividers
    out.append('  <g stroke="#DDE3EB" stroke-width="1">')
    for i in range(n_months + 1):
        x = LEFT_MARGIN + i * col_w
        out.append(
            f'    <line x1="{x:.1f}" y1="{HEADER_H + AXIS_H}" '
            f'x2="{x:.1f}" y2="{HEADER_H + AXIS_H + EVENTS_H}"/>'
        )
    out.append('  </g>')

    # Horizontal lane band
    out.append(
        f'  <rect x="0" y="{HEADER_H + AXIS_H}" width="{W}" height="{EVENTS_H}" '
        f'fill="#0B1E36" fill-opacity="0.02"/>'
    )

    # Pills
    out.append('  <g font-size="11" fill="#0B1E36">')
    pill_y_top = HEADER_H + AXIS_H + STACK_TOP_PAD
    for i, mi in enumerate(months):
        evs = by_month.get(mi, [])
        cx = LEFT_MARGIN + (i + 0.5) * col_w
        for j, e in enumerate(evs):
            pill_x = cx - pill_w / 2
            pill_y = pill_y_top + j * (PILL_H + PILL_GAP)
            stroke, fill = STATUS_PALETTE.get(e["status"], STATUS_PALETTE["Update"])
            label_main = truncate(e["title"], pill_text_chars)
            sub = e["_dt"].strftime("%-m/%-d")
            out.append(
                f'    <rect x="{pill_x:.1f}" y="{pill_y}" width="{pill_w:.1f}" height="{PILL_H}" '
                f'rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            )
            out.append(
                f'    <text x="{cx:.1f}" y="{pill_y + 14}" text-anchor="middle" '
                f'font-weight="600">{xml_escape(label_main)}</text>'
            )
            out.append(
                f'    <text x="{cx:.1f}" y="{pill_y + 26}" text-anchor="middle" '
                f'font-size="9" fill="#5A7088">{sub}  ·  {xml_escape(e["status"])}</text>'
            )
    out.append('  </g>')

    # Legend
    legend_y = HEADER_H + AXIS_H + EVENTS_H + 20
    out.append('  <g font-size="13" fill="#0B1E36">')
    out.append(
        f'    <text x="40" y="{legend_y}" font-weight="700" font-size="14">Status</text>'
    )
    lx = 130
    for status in ["GA", "Public Preview", "Private Preview", "Beta", "Update"]:
        stroke, fill = STATUS_PALETTE[status]
        out.append(
            f'    <rect x="{lx}" y="{legend_y - 16}" width="20" height="20" rx="4" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )
        out.append(f'    <text x="{lx + 28}" y="{legend_y}">{status}</text>')
        lx += 28 + 8 * len(status) + 28
    out.append('  </g>')

    # Footer
    footer_y = H - 12
    out.append(
        f'  <text x="40" y="{footer_y}" font-size="11" fill="#5A7088">'
        f'Source: docs.databricks.com/aws/en/release-notes/product/  ·  '
        f'classified into "{bucket}" bucket  ·  generated by feature-timeline.py'
        f'</text>'
    )

    out.append('</svg>\n')
    return "\n".join(out)

# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="features-classified.json")
    ap.add_argument("--out-dir", default="by-product")
    ap.add_argument("--min-events", type=int, default=3,
                    help="skip buckets with fewer events (default: 3)")
    ap.add_argument("--render-png", action="store_true",
                    help="run rsvg-convert on each SVG to produce a PNG")
    ap.add_argument("--png-width", type=int, default=2400)
    args = ap.parse_args()

    events = json.load(open(args.input))

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_bucket[e["bucket"]].append(e)

    os.makedirs(args.out_dir, exist_ok=True)
    written = []
    skipped = []
    for bucket, evs in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        if len(evs) < args.min_events:
            skipped.append((bucket, len(evs)))
            continue
        svg = build_svg(bucket, evs)
        svg_path = os.path.join(args.out_dir, f"{bucket}.svg")
        with open(svg_path, "w") as f:
            f.write(svg)
        written.append((bucket, len(evs), svg_path))
        print(f"  {bucket:18s} {len(evs):3d} events → {svg_path}", file=sys.stderr)

    if args.render_png:
        rsvg = shutil.which("rsvg-convert")
        if not rsvg:
            print("\nrsvg-convert not on PATH — skipping PNG render.", file=sys.stderr)
            print("  Install: brew install librsvg", file=sys.stderr)
        else:
            print(f"\nrendering PNGs (rsvg-convert -w {args.png_width})...", file=sys.stderr)
            for bucket, _, svg_path in written:
                png_path = svg_path.replace(".svg", ".png")
                subprocess.run([rsvg, "-w", str(args.png_width), svg_path, "-o", png_path],
                               check=True)
                print(f"  → {png_path}", file=sys.stderr)

    if skipped:
        print(f"\nskipped (fewer than {args.min_events} events): "
              f"{', '.join(f'{b} ({n})' for b, n in skipped)}", file=sys.stderr)
    print(f"\nwrote {len(written)} timelines to {args.out_dir}/", file=sys.stderr)

if __name__ == "__main__":
    main()
