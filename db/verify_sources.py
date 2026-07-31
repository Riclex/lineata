#!/usr/bin/env python3
"""
Source URL liveness checker for the Angola Investment Execution Database.

For each source with a non-empty `url`, issues a HEAD request (falling back to
GET) and records:
  - last_verified  = today's date (YYYY-MM-DD)
  - url_status     = 'alive' | 'blocked' | 'dead'
Sources with an empty URL (publisher-only records) are stamped 'n/a'. Results
are written back to data/sources.csv so they survive rebuilds (load.py loads
last_verified / url_status from CSV).

This operationalises the goal's "latest verification" field and the strategic
risk it names: "the largest risk isn't collecting data — it's maintaining it.
Continuous verification is expensive." A dated liveness stamp per source makes
stale/dead links visible instead of silent.

Usage:
    python db/verify_sources.py             # check and print, write nothing
    python db/verify_sources.py --apply      # check and write results to sources.csv + DB
    python db/verify_sources.py --limit 10   # check only the first 10 URL sources (smoke test)
"""

import csv
import os
import sys
import sqlite3
import argparse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import log_change  # shared change_log writer (db/audit.py)

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_dir = os.path.join(BASE_dir, "data")
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
SOURCES_csv = os.path.join(DATA_dir, "sources.csv")

TIMEOUT = 8
UA = "Mozilla/5.0 (compatible; FILDA-tracker/1.0; +source-liveness-check)"


def classify(url):
    """Return (status, http_code). status in {'alive','blocked','dead'}."""
    headers = {"User-Agent": UA}
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return "alive", r.status
        except urllib.error.HTTPError as e:
            # 4xx: blocked (exists but access-denied) for 401/403; dead for 404/410.
            if e.code in (401, 403):
                return "blocked", e.code
            if e.code in (404, 410):
                return "dead", e.code
            # Other 4xx/5xx: treat as dead (link not usable).
            return "dead", e.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            # Connection failure / timeout / bad URL — try GET before declaring dead.
            if method == "HEAD":
                continue
            return "dead", None
    return "dead", None


def main():
    parser = argparse.ArgumentParser(description="Check source URL liveness")
    parser.add_argument("--apply", action="store_true", help="Write results to sources.csv + DB")
    parser.add_argument("--limit", type=int, help="Only check the first N URL sources")
    args = parser.parse_args()

    with open(SOURCES_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    url_rows = [r for r in rows if r.get("url", "").strip()]
    if args.limit:
        url_rows = url_rows[:args.limit]

    print(f"Checking {len(url_rows)} of {sum(1 for r in rows if r.get('url','').strip())} "
          f"URL sources (total {len(rows)} sources)...")
    counts = {"alive": 0, "blocked": 0, "dead": 0, "n/a": 0}
    results = {}  # id -> (status, http_code)
    for i, r in enumerate(url_rows, 1):
        sid = r["id"]
        url = r["url"].strip()
        status, code = classify(url)
        results[sid] = (status, code)
        counts[status] += 1
        marker = f"{code}" if code else "-"
        print(f"  [{i:>3}] {sid:>3} {status:>7} ({marker}) {url[:70]}")

    # Publisher-only (empty URL) sources get 'n/a'.
    for r in rows:
        if not r.get("url", "").strip():
            results[r["id"]] = ("n/a", None)
            counts["n/a"] += 1

    print(f"\nSummary: alive={counts['alive']} blocked={counts['blocked']} "
          f"dead={counts['dead']} n/a={counts['n/a']}")

    if not args.apply:
        print("(dry run — sources.csv not modified. Re-run with --apply to persist.)")
        return

    # Write back to sources.csv (preserves column order; persists across rebuilds).
    from datetime import date
    today = date.today().isoformat()
    changed_ids = []  # ids whose last_verified / url_status actually changed
    for r in rows:
        sid = r["id"]
        if sid in results:
            new_status = results[sid][0]
            if r.get("last_verified", "") != today or r.get("url_status", "") != new_status:
                r["last_verified"] = today
                r["url_status"] = new_status
                changed_ids.append(sid)
    with open(SOURCES_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] data/sources.csv updated: {len(changed_ids)} source(s) re-stamped (last_verified={today})")

    # Also reflect into the live DB so the same run's DB matches, AND write one
    # change_log 'reverify' row per actually-changed source. This closes the audit
    # gap: --apply used to mutate sources.last_verified / url_status with no
    # change_log row, so the reverify audit trail (who/when/old->new) was lost and
    # the staleness guard (which counts 'reverify' ops) couldn't see these
    # mutations. Routing the liveness stamp through log_change makes the audit
    # trail complete and brings these mutations under load.py's staleness guard.
    url_by_id = {r["id"]: r.get("url", "") for r in rows}
    conn = sqlite3.connect(DB_path, isolation_level=None)
    conn.execute("BEGIN")
    n = 0
    try:
        for sid in changed_ids:
            status = results[sid][0]
            old = conn.execute(
                "SELECT last_verified, url_status FROM sources WHERE id=?",
                (sid,)).fetchone()
            old_lv, old_status = (old[0], old[1]) if old else (None, None)
            conn.execute(
                "UPDATE sources SET last_verified=?, url_status=? WHERE id=?",
                (today, status, sid))
            log_change(conn, "reverify", "sources", sid,
                       {"old_status": old_status, "new_status": status,
                        "old_last_verified": old_lv, "new_last_verified": today},
                       url_by_id.get(sid) or None, None)
            n += 1
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        sys.exit(f"[ERR] verify_sources DB write failed: {e}")
    conn.close()
    print(f"[OK] db/investment_tracker.db updated: {n} source(s) re-stamped + audit-logged.")


if __name__ == "__main__":
    main()