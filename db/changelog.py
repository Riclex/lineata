#!/usr/bin/env python3
"""
Change-log digest viewer for the Angola Investment Execution Database.

The append-only `change_log` table is the audit trail of every mutation made
through db/update.py (plus the load-seed / export-csv checkpoint markers). It
is written on every edit but nothing reads it back for a human overview — this
script does. Read-only; opens the DB in immutable mode and never writes.

It answers, at a glance:
  - Is the DB checkpointed, or are there uncheckpointed mutations? (staleness)
  - What kinds of mutations have been made, and how many of each?
  - Which project scores have moved, and from what to what?
  - What new sources have been added?
  - Is the "only Banco Sol is unsourced" invariant still intact?

Usage:
    python db/changelog.py                 # full digest
    python db/changelog.py --since 2026-07-30   # only mutations on/after a date
    python db/changelog.py --movers         # just the score movers
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "investment_tracker.db")

# Operations that represent real data mutations (vs. the load-seed/export-csv
# markers). Matches the staleness-guard set in load.py / verify.py.
MUTATION_OPS = ("add-source", "add-event", "add-evidence", "set-status",
                "relink-event", "relink-evidence", "reverify")


def parse_payload(row):
    try:
        return json.loads(row["payload_json"]) if row["payload_json"] else {}
    except json.JSONDecodeError:
        return {}


def main():
    parser = argparse.ArgumentParser(description="Digest the change_log audit trail.")
    parser.add_argument("--since", help="Only show mutations on/after this date (YYYY-MM-DD)")
    parser.add_argument("--movers", action="store_true", help="Only show score movers")
    args = parser.parse_args()

    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")

    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    since_clause = "AND ts >= ?" if args.since else ""
    since_params = (args.since,) if args.since else ()

    # ---- Checkpoint status ----
    le = conn.execute("SELECT value FROM db_meta WHERE key='last_exported_at'").fetchone()
    sv = conn.execute("SELECT value FROM db_meta WHERE key='score_version'").fetchone()
    last_export = le[0] if le else None
    score_version = sv[0] if sv else None

    if not args.movers:
        print("Change-log digest")
        print("=" * 64)
        print(f"  last checkpoint (last_exported_at): {last_export}")
        print(f"  score version:                      {score_version}")

    # Uncheckpointed mutations: real mutations newer than the watermark.
    if last_export:
        uncommitted = conn.execute(
            f"SELECT COUNT(*) FROM change_log WHERE operation IN ({','.join('?' * len(MUTATION_OPS))}) "
            f"AND ts > ?",
            (*MUTATION_OPS, last_export)).fetchone()[0]
        if not args.movers:
            if uncommitted:
                print(f"  uncheckpointed mutations:            {uncommitted}  ⚠ run `python db/export_csv.py --apply`")
            else:
                print(f"  uncheckpointed mutations:            0  (checkpoint is current)")

    # ---- Mutation rows (filtered by --since) ----
    rows = conn.execute(
        f"SELECT * FROM change_log WHERE operation IN ({','.join('?' * len(MUTATION_OPS))}) "
        f"{since_clause} ORDER BY id",
        (*MUTATION_OPS, *since_params)).fetchall()

    if not args.movers:
        print()
        print(f"Mutations{' since ' + args.since if args.since else ''}: {len(rows)}")
        print("-" * 64)
        op_counts = Counter(r["operation"] for r in rows)
        if op_counts:
            width = max(len(op) for op in op_counts)
            for op, n in sorted(op_counts.items()):
                print(f"  {op:<{width}}  {n}")
        else:
            print("  (none)")

    # ---- Score movers ----
    movers = []
    for r in rows:
        p = parse_payload(r)
        old, new = p.get("score_old"), p.get("score_new")
        if old is None or new is None:
            continue
        if old != new:
            movers.append((r["ts"], r["operation"], p.get("project_id"), old, new))

    print()
    print(f"Score movers ({len(movers)}):")
    print("-" * 64)
    if movers:
        for ts, op, pid, old, new in movers:
            print(f"  {ts}  {op:<16} {pid:<40} {old} -> {new}")
    else:
        print("  (none — no mutation changed a score)")

    if not args.movers:
        # ---- New sources ----
        # Sources enter the DB two ways: an explicit `add-source` op (payload
        # has 'id'), or inline via resolve_or_create_source inside an add-event
        # / add-evidence / set-status / relink op (payload has source_created=True
        # and a source_id / source_id_new). Surface both so the digest is complete.
        new_source_ids = set()
        for r in rows:
            p = parse_payload(r)
            if r["operation"] == "add-source" and p.get("id") is not None:
                new_source_ids.add(p["id"])
            elif p.get("source_created") is True:
                sid = p.get("source_id") or p.get("source_id_new")
                if sid is not None:
                    new_source_ids.add(sid)
        new_sources = []
        for sid in sorted(new_source_ids):
            s = conn.execute("SELECT title, publisher FROM sources WHERE id = ?", (sid,)).fetchone()
            new_sources.append((sid, s["publisher"] if s else "?", s["title"] if s else "?"))
        print()
        print(f"New sources added ({len(new_sources)}):")
        print("-" * 64)
        if new_sources:
            for sid, pub, title in new_sources:
                print(f"  source {sid:<4} {pub or '?':<20} {(title or '?')[:40]}")
        else:
            print("  (none)")

        # ---- Unsourced-event invariant ----
        null_events = [r[0] for r in conn.execute(
            "SELECT id FROM events WHERE source_id IS NULL ORDER BY id")]
        print()
        print("Unsourced-event invariant:")
        print("-" * 64)
        if null_events == [80]:
            print(f"  {len(null_events)} NULL event(s): {null_events}  ✓ only Banco Sol (as required)")
        else:
            print(f"  ⚠ {len(null_events)} NULL event(s): {null_events}  (expected [80] — investigate)")

    conn.close()


if __name__ == "__main__":
    main()