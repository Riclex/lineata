#!/usr/bin/env python3
"""
Expand project_evidence to the non-case-study projects.

The 7 published case studies were hand-curated (14 rows). This script applies
the SAME documented convention to the remaining projects, mechanically, so
every tracked project's key fields are backed by a click-through source:

  field          <- the source_id of the event that establishes it
  observed_at    <- that event's date

Convention (field -> preferred event types, first sourced event wins):
  status          : status_map (completed->completion, under_construction->
                    construction/groundbreaking/financing, operational->
                    completion/expansion/..., announced->announcement, ...)
  announced_value : financing / announcement / mou
  estimated_jobs  : construction / groundbreaking / financing / announcement
  actual_completion: completion

Discipline: NEVER fabricate a source. If a field has a value but no sourced
event of the matching type exists, that field is SKIPPED (left without a
provenance row) rather than linked to an unrelated source. So the number of
rows generated is a lower bound on what's provable, not a guess.

Dry run by default. Writes data/project_evidence.csv AND the DB only with
--apply (then re-run db/load.py to rebuild from the CSV).

    python db/_extract/expand_evidence.py            # dry run, print the plan
    python db/_extract/expand_evidence.py --apply     # append to CSV + DB
"""

import csv
import os
import sqlite3
import sys

BASE_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_dir = os.path.join(BASE_dir, "data")
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
EVIDENCE_csv = os.path.join(DATA_dir, "project_evidence.csv")

# Projects already hand-curated (the 7 published case studies) — single-sourced
# from db/constants.py (L6) so this extractor can never drift from the verifier.
sys.path.insert(0, os.path.join(BASE_dir, "db"))
from constants import CASE_STUDIES  # noqa: E402

STATUS_MAP = {
    "completed": ["completion"],
    "under_construction": ["construction", "groundbreaking", "financing"],
    "operational": ["completion", "expansion", "construction", "groundbreaking",
                    "announcement"],
    "restarted": ["restart"],
    "financed": ["financing"],
    "mou_signed": ["mou"],
    "announced": ["announcement"],
    "delayed": ["delay"],
    "suspended": ["suspension"],
    "cancelled": ["closure"],
    "unknown": [],
}

FIELD_EVENTS = {
    "announced_value": ["financing", "announcement", "mou"],
    "estimated_jobs": ["construction", "groundbreaking", "financing", "announcement"],
    "actual_completion": ["completion"],
}


def first_sourced_event(events, preferred_types):
    """Return the first event (earliest date) whose type is in preferred_types
    and that has a non-null source_id, or None."""
    candidates = [e for e in events
                 if e["event_type"] in preferred_types and e["source_id"] is not None]
    if not candidates:
        return None
    # earliest event first; None dates sort last
    candidates.sort(key=lambda e: e["event_date"] or "9999")
    return candidates[0]


def main():
    apply = "--apply" in sys.argv

    conn = sqlite3.connect(DB_path)
    conn.row_factory = sqlite3.Row

    # Existing evidence project ids (skip — already curated/covered).
    with open(EVIDENCE_csv, newline="", encoding="utf-8") as f:
        existing = {r["project_id"] for r in csv.DictReader(f)}

    projects = conn.execute(
        "SELECT id, status, announced_value, estimated_jobs, actual_completion "
        "FROM projects ORDER BY id"
    ).fetchall()

    new_rows = []
    skipped_fields = []  # (project_id, field) — value present but no sourced event
    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM project_evidence").fetchone()[0] + 1

    for p in projects:
        pid = p["id"]
        if pid in CASE_STUDIES or pid in existing:
            continue

        events = conn.execute(
            "SELECT event_type, event_date, source_id FROM events "
            "WHERE project_id = ?", (pid,)
        ).fetchall()
        events = [dict(e) for e in events]

        # status
        if p["status"]:
            pref = STATUS_MAP.get(p["status"], [])
            ev = first_sourced_event(events, pref) if pref else None
            if ev:
                new_rows.append((next_id, pid, "status", p["status"],
                                ev["source_id"], ev["event_date"]))
                next_id += 1
            elif pref:
                skipped_fields.append((pid, "status"))

        # numeric/text fields
        for field, pref in FIELD_EVENTS.items():
            val = p[field]
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            ev = first_sourced_event(events, pref)
            if ev:
                new_rows.append((next_id, pid, field, str(val),
                                ev["source_id"], ev["event_date"]))
                next_id += 1
            else:
                skipped_fields.append((pid, field))

    conn.close()

    # Report
    projects_covered = sorted({r[1] for r in new_rows})
    print(f"Projects newly covered: {len(projects_covered)}")
    print(f"New evidence rows: {len(new_rows)}")
    print(f"Fields skipped (value present, no sourced event): "
          f"{len(skipped_fields)}")
    by_field = {}
    for r in new_rows:
        by_field[r[2]] = by_field.get(r[2], 0) + 1
    for field, n in sorted(by_field.items()):
        print(f"  {field}: {n} rows")

    if skipped_fields:
        print("\nSkipped (no fabricated link — these fields stay unprovenanced):")
        for pid, field in skipped_fields:
            print(f"  {pid}: {field}")

    if not apply:
        print("\n(dry run — project_evidence.csv and DB not modified)")
        return

    # Append to CSV
    with open(EVIDENCE_csv, newline="", encoding="utf-8") as f:
        existing_rows = list(csv.DictReader(f))
        fieldnames = list(existing_rows[0].keys())
    for row in new_rows:
        existing_rows.append({
            "id": row[0], "project_id": row[1], "field": row[2],
            "value": row[3], "source_id": row[4], "observed_at": row[5] or "",
        })
    with open(EVIDENCE_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(existing_rows)
    print(f"\n[OK] project_evidence.csv: appended {len(new_rows)} rows "
          f"({len(existing_rows)} total)")

    # Append to DB
    conn = sqlite3.connect(DB_path)
    conn.executemany(
        "INSERT INTO project_evidence (id, project_id, field, value, "
        "source_id, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
        new_rows,
    )
    conn.commit()
    conn.close()
    print(f"[OK] DB: inserted {len(new_rows)} evidence rows")


if __name__ == "__main__":
    main()