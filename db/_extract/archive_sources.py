#!/usr/bin/env python3
"""
Backfill sources.archived_url for dead / blocked source URLs.

For every source whose url_status is 'dead' or 'blocked' and that has no
archived_url yet, query the Wayback Machine availability API and record the
closest snapshot URL. This is the honest way to preserve a dead link: we point
to a real, API-verified web.archive.org snapshot — never a fabricated URL.

Source 12 is a special case: its `url` field is mojibake-corrupted (cp1252
console damage on the original RFI PT URL). Source 29 is the same RFI article
with a clean URL, so we query the Wayback using 29's clean URL and assign the
resulting archive snapshot to both 12 and 29. (Deduping 12 into 29 is a
separate, larger change; this script only backfills archived_url.)

Dry run by default. Writes to data/sources.csv AND the DB only with --apply.

    python db/_extract/archive_sources.py            # dry run, print the plan
    python db/_extract/archive_sources.py --apply     # write sources.csv + DB
"""

import csv
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request

BASE_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_dir = os.path.join(BASE_dir, "data")
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
SOURCES_csv = os.path.join(DATA_dir, "sources.csv")

API = "https://archive.org/wayback/available"
UA = "Mozilla/5.0 (FILDA-Tracker archive-backfill)"
TIMEOUT = 15

# source id -> clean URL to query when the source's own url is unusable.
# 12's url is mojibake-corrupted; 29 is the same RFI article, clean.
CLEAN_URL_OVERRIDE = {
    "12": "https://www.rfi.fr/pt/angola/20230718-38-edicao-da-filda-arrancou-nesta-terca-feira-em-luanda",
}


def query_wayback(url, timestamp=None):
    """Return the closest archived snapshot URL for `url`, or None.

    Uses the Wayback availability API. `timestamp` (YYYYMMDD) biases the
    'closest' selection toward a snapshot near the article's publication date.
    """
    params = {"url": url}
    if timestamp:
        # normalise to YYYYMMDD (strip non-digits, take first 8)
        digits = "".join(ch for ch in str(timestamp) if ch.isdigit())[:8]
        if len(digits) >= 4:
            params["timestamp"] = digits
    q = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(q, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except Exception as e:
        return None, f"API error: {type(e).__name__}: {e}"
    snap = (data.get("archived_snapshots") or {}).get("closest")
    if not snap or not snap.get("available"):
        return None, "no snapshot"
    return snap.get("url"), "ok"


def main():
    apply = "--apply" in sys.argv

    with open(SOURCES_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys())

    targets = [r for r in rows
              if r.get("url_status") in ("dead", "blocked")
              and not (r.get("archived_url") or "").strip()]

    print(f"Dead/blocked sources without archived_url: {len(targets)}")
    print(f"{'id':>4}  {'status':<8}  {'result':<6}  url / archived")
    print("-" * 100)

    plan = []  # (id_str, archived_url)
    for r in targets:
        sid = r["id"]
        query_url = CLEAN_URL_OVERRIDE.get(sid) or r["url"]
        archived, status = query_wayback(query_url, r.get("date"))
        if archived:
            plan.append((sid, archived))
            print(f"{sid:>4}  {r['url_status']:<8}  {status:<6}  {query_url}")
            print(f"          -> {archived}")
        else:
            print(f"{sid:>4}  {r['url_status']:<8}  {status:<6}  {query_url}")

    print("-" * 100)
    print(f"Snapshots found: {len(plan)} / {len(targets)}")

    if not apply:
        print("(dry run — sources.csv and DB not modified)")
        return

    # Write to sources.csv
    by_id = {r["id"]: r for r in rows}
    for sid, archived in plan:
        if sid in by_id:
            by_id[sid]["archived_url"] = archived
    with open(SOURCES_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"[OK] sources.csv: wrote archived_url for {len(plan)} source(s)")

    # Write to DB
    conn = sqlite3.connect(DB_path)
    for sid, archived in plan:
        conn.execute("UPDATE sources SET archived_url = ? WHERE id = ?",
                     (archived, int(sid)))
    conn.commit()
    conn.close()
    print(f"[OK] DB: updated archived_url for {len(plan)} source(s)")


if __name__ == "__main__":
    main()