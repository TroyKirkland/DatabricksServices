#!/usr/bin/env python3
"""Scrape Databricks AWS release notes for feature-level events (GA / Preview / Beta).

Source: https://docs.databricks.com/aws/en/release-notes/product/{year}/{month}
Output: JSON list of {date, title, slug, status, url, year, month}.

Usage:
    python3 feature-scraper.py 2026-01 2026-05
    python3 feature-scraper.py 2025-12 2026-05 -o events.json
    python3 feature-scraper.py 2026-05 2026-05 --status GA      # only GA milestones
"""
import argparse
import json
import re
import sys
import urllib.request

URL_TEMPLATE = "https://docs.databricks.com/aws/en/release-notes/product/{year}/{month}"
MONTHS = ["january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december"]

# Each entry: <h2 ... id=slug>Title<a ...hash-link...>...</a></h2><p><strong>DATE</strong></p>
ENTRY_RE = re.compile(
    r'<h2[^>]*\bid=(?P<slug>[A-Za-z0-9_-]+)>'
    r'(?P<title>[^<]+)'
    r'<a[^>]*hash-link[^>]*>[^<]*</a>\s*</h2>\s*'
    r'<p><strong>(?P<date>[A-Z][a-z]+\s+\d{1,2},\s+\d{4})</strong></p>',
    re.DOTALL,
)

# Order matters: more specific status first.
STATUS_PATTERNS = [
    (re.compile(r'\b(?:is\s+now\s+|are\s+now\s+|now\s+)?generally\s+available\b', re.I), "GA"),
    (re.compile(r'\(\s*GA\s*\)|\bGA\b'), "GA"),
    (re.compile(r'\bpublic\s+preview\b', re.I), "Public Preview"),
    (re.compile(r'\bprivate\s+preview\b', re.I), "Private Preview"),
    (re.compile(r'\(\s*beta\s*\)|\bbeta\b', re.I), "Beta"),
]

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "release-notes-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def classify(title: str) -> str:
    for pat, label in STATUS_PATTERNS:
        if pat.search(title):
            return label
    return "Update"

def parse_month(year: int, month_name: str) -> list[dict]:
    url = URL_TEMPLATE.format(year=year, month=month_name)
    html = fetch(url)
    out = []
    for m in ENTRY_RE.finditer(html):
        title = m.group("title").strip()
        out.append({
            "date": m.group("date"),
            "year": year,
            "month": month_name,
            "title": title,
            "slug": m.group("slug"),
            "status": classify(title),
            "url": f"{url}#{m.group('slug')}",
        })
    return out

def month_range(start: str, end: str):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield (y, MONTHS[m - 1])
        m += 1
        if m > 12:
            m, y = 1, y + 1

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("start", help="start month, YYYY-MM (e.g. 2026-01)")
    ap.add_argument("end",   help="end month, YYYY-MM (inclusive)")
    ap.add_argument("-o", "--out", help="output JSON file (default: stdout)")
    ap.add_argument("--status", action="append",
                    help="filter to one status (repeatable): GA | Public Preview | Private Preview | Beta | Update")
    args = ap.parse_args()

    all_events = []
    for year, month in month_range(args.start, args.end):
        try:
            events = parse_month(year, month)
            print(f"  {year}-{month:9s} {len(events):3d} entries", file=sys.stderr)
            all_events.extend(events)
        except Exception as e:
            print(f"  {year}-{month}: ERROR {e}", file=sys.stderr)

    if args.status:
        wanted = set(args.status)
        all_events = [e for e in all_events if e["status"] in wanted]

    counts = {}
    for e in all_events:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"\ntotal {len(all_events)}  ·  by status: {counts}", file=sys.stderr)

    payload = json.dumps(all_events, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
        print(f"→ {args.out}", file=sys.stderr)
    else:
        print(payload)

if __name__ == "__main__":
    main()
