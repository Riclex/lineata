#!/usr/bin/env python3
"""
One-time migration (2026-07-25) that adds the new provenance/verification
columns to the CSVs and creates the project_evidence.csv skeleton.

Adds:
  projects.csv        + last_verified (backfilled 2026-07-25, the last full
                      reconciliation) + created_at (backfilled 2026-07-23, the
                      schema/DB origin date — an honest "dataset established"
                      date, NOT a per-row first-seen timestamp, since those
                      were never recorded)
  organizations.csv   + created_at (2026-07-23)
  sources.csv         + last_verified (blank — stamped by db/verify_sources.py)
                      + url_status (blank)
  project_evidence.csv (new, header only — backfilled separately)

Run once:
    python db/_extract/add_columns.py
Idempotent: it checks whether the columns already exist and skips if so.
"""

import csv
import os

BASE_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_dir = os.path.join(BASE_dir, "data")

PROJECTS_BACKFILL = {"last_verified": "2026-07-25", "created_at": "2026-07-23"}
ORGS_BACKFILL = {"created_at": "2026-07-23"}
SOURCES_BACKFILL = {"last_verified": "", "url_status": ""}


def add_columns(filename, new_cols):
    """Append new columns to filename (in given order), backfilling each row."""
    path = os.path.join(DATA_dir, filename)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    already = [c for c in new_cols if c in fieldnames]
    if already:
        print(f"  {filename}: already has {already} — skipping")
        return
    fieldnames = fieldnames + list(new_cols.keys())
    for row in rows:
        for col, val in new_cols.items():
            row.setdefault(col, val)
    with open(path, "w", newline="", encoding="utf-8") as f:
        # extrasaction='ignore' so a row with a spurious extra column (the
        # organizations.csv ragged-row bug) doesn't abort the migration.
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {filename}: added {list(new_cols.keys())} ({len(rows)} rows)")


def create_evidence_skeleton():
    path = os.path.join(DATA_dir, "project_evidence.csv")
    if os.path.exists(path):
        print("  project_evidence.csv: already exists — skipping")
        return
    fieldnames = ["id", "project_id", "field", "value", "source_id", "observed_at"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
    print(f"  project_evidence.csv: created skeleton (header only)")


def main():
    print("Adding provenance/verification columns:")
    add_columns("projects.csv", PROJECTS_BACKFILL)
    add_columns("organizations.csv", ORGS_BACKFILL)
    add_columns("sources.csv", SOURCES_BACKFILL)
    create_evidence_skeleton()
    print("\nDone. Rebuild with `python db/load.py`.")


if __name__ == "__main__":
    main()