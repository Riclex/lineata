#!/usr/bin/env python3
"""
DB -> CSV checkpointer for the Angola Investment Execution Database.

The companion to db/update.py. After an incremental append session, run this to
refresh data/*.csv from the live DB so db/load.py can still reproduce the
database from CSV checkpoints (the reproducibility guarantee). It also stamps
the db_meta.last_exported_at watermark that load.py's staleness guard checks
before any rebuild.

Key property: the projects.csv `execution_score` column is exported from
`compute_scores(conn)` — the computed formula value, NOT the stored column.
So if update.py ever has a recompute bug, the checkpoint self-heals and
load.py's score-consistency gate stays a real backstop instead of rubber-
stamping stale values.

Column order: each existing CSV's on-disk header order is preserved (minimal
git diff). load.py uses csv.DictReader (maps by name) so any order round-trips.
For the two new tables (change_log, db_meta) the header is the schema
declaration order.

Usage:
    python db/export_csv.py          # print counts + score drift, write nothing
    python db/export_csv.py --apply  # write all 8 CSVs + stamp db_meta + log
"""

import csv
import json
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_scores import compute_scores  # db/calculate_scores.py:230

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_dir = os.path.join(BASE_dir, "data")
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")

# Export order: dependencies don't matter (read-only), but keep load.py order
# for readability. change_log and db_meta are appended.
TABLES = [
    "sources", "projects", "organizations", "project_organizations",
    "events", "project_evidence", "change_log", "db_meta",
]

# Headers for tables that may not have an on-disk CSV yet (the two new ones).
FALLBACK_HEADERS = {
    "change_log": ["id", "ts", "operation", "target_table", "target_id",
                   "payload_json", "source_url", "note"],
    "db_meta": ["key", "value"],
}


def header_of(table):
    """On-disk CSV header if the file exists, else the fallback declaration order."""
    path = os.path.join(DATA_dir, f"{table}.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            return next(csv.reader(f), None)
    return FALLBACK_HEADERS.get(table)


def export_table(conn, table, score_by_id):
    """Write data/<table>.csv from the current DB, preserving the on-disk
    header order. For projects, execution_score is overridden with the computed
    value. Returns the row count written."""
    cols = header_of(table)
    if not cols:
        raise RuntimeError(f"no header known for table {table!r}")
    select_cols = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(f'SELECT {select_cols} FROM "{table}"').fetchall()

    out = []
    for r in rows:
        d = {c: r[c] for c in cols}
        if table == "projects" and "execution_score" in d:
            pid = d.get("id")
            if pid in score_by_id:
                d["execution_score"] = score_by_id[pid][0]
        out.append({c: ("" if d[c] is None else d[c]) for c in cols})

    path = os.path.join(DATA_dir, f"{table}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    return len(out)


def main():
    if "--apply" not in sys.argv:
        apply = False
    else:
        apply = True

    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")

    conn = sqlite3.connect(DB_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    score_by_id = compute_scores(conn)

    # Score drift: stored execution_score vs formula. Any drift means update.py's
    # recompute is out of sync with the formula — exactly what this checkpoint
    # (and load.py's gate) should surface.
    stored = {pid: s for pid, s in
              conn.execute("SELECT id, execution_score FROM projects")}
    drift = [(pid, stored.get(pid), comp)
             for pid, (comp, _b) in score_by_id.items() if stored.get(pid) != comp]

    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES}

    print(f"DB row counts: {counts}")
    if drift:
        print(f"\n[!] score drift (stored != formula) for {len(drift)} project(s):")
        for pid, s, c in drift:
            print(f"  {pid:<42} stored={s} computed={c}")
    else:
        print("score drift: none (stored == formula for all projects)")

    if not apply:
        print("\n(dry run — CSVs not modified. Re-run with --apply to checkpoint.)")
        conn.close()
        return

    # 1) Stamp the watermark + write the export-csv audit row first, so the
    #    change_log.csv and db_meta.csv we export below include them.
    conn.execute("BEGIN")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_meta (key, value) VALUES ('last_exported_at', NULL)")
        conn.execute(
            "UPDATE db_meta SET value=datetime('now') WHERE key='last_exported_at'")
        conn.execute(
            "INSERT INTO change_log (operation, target_table, target_id, payload_json, "
            "source_url, note) VALUES ('export-csv', 'db_meta', NULL, ?, NULL, 'checkpoint refresh')",
            (json.dumps({"rows_exported": sum(counts.values()), "tables": counts,
                         "score_drift": len(drift)}, ensure_ascii=False),))
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] failed to stamp watermark: {e}")

    # 2) Export every table. Score column is computed (see export_table).
    written = {}
    for t in TABLES:
        written[t] = export_table(conn, t, score_by_id)

    conn.close()
    print(f"\n[OK] checkpoint written: {written}")
    print("  db_meta.last_exported_at stamped; change_log export-csv row recorded.")
    print("  Next `python db/load.py` will reproduce this DB from CSV (staleness guard will pass).")


if __name__ == "__main__":
    main()