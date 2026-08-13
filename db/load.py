#!/usr/bin/env python3
"""
CSV -> SQLite loader for the Angola Investment Execution Database.

Rebuilds db/investment_tracker.db from schema.sql and the CSV files in data/.
Idempotent: running it again produces the same database.

Pre-load gate (in load_csv, before the DB is touched): every CSV's header must
match COLUMNS exactly and every data row must have the same field count as the
header — a reordered/renamed column or a short row fails fast instead of
silently mis-mapping values (the 2026-08 filda_edition shift bug).

Integrity gates (run after the load, with foreign_keys re-enabled):
  1. PRAGMA foreign_key_check — fails the load on any dangling FK.
  2. execution_score consistency — recomputes scores from the loaded data and
     asserts they match the snapshot stored in projects.csv. A mismatch means
     the CSV snapshot is stale; run `python db/calculate_scores.py --update-csv`
     to sync it, then re-load.
  3. data_completeness consistency — recomputes the timeline-completeness label
     from the loaded events and asserts it matches the snapshot stored in
     projects.csv (mirrors gate 2; see constants.data_completeness).

Usage:
    python db/load.py            # rebuild the database from CSVs (with integrity gates)
    python db/load.py --dry      # validate CSVs and print counts without writing
"""

import csv
import os
import sys
import sqlite3
import argparse

# Import the scoring logic so the loader can verify the execution_score
# snapshot in projects.csv against the formula on every rebuild.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_scores import compute_scores, SCORE_VERSION
from constants import MUTATION_OPS, data_completeness  # staleness-guard op set + data_completeness derivation

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_dir = os.path.join(BASE_dir, "data")
DB_dir = os.path.join(BASE_dir, "db")
DB_path = os.path.join(DB_dir, "investment_tracker.db")
SCHEMA_path = os.path.join(DB_dir, "schema.sql")

# Load order respects logical dependencies; FKs are disabled during load
# (organizations.parent_org_id is self-referential and parents may follow children).
TABLES = [
    "sources",
    "projects",
    "organizations",
    "project_organizations",
    "events",
    "project_evidence",
    "change_log",
    "db_meta",
]

# Columns written from each CSV, in file order. created_at is loaded from CSV
# so first-seen timestamps survive rebuilds (only updated_at is left to
# SQLite's DEFAULT datetime('now')). This is ALSO the expected on-disk header
# (load_csv validates against it), so it must match data/<table>.csv exactly —
# including is_externally_blocked, which export_csv.py round-trips and which a
# rebuild would otherwise silently drop (DB default 0).
COLUMNS = {
    "sources": ["id", "title", "url", "date", "publisher", "archived_url",
                "confidence", "last_verified", "url_status"],
    "projects": [
        "id", "title", "sector", "subsector", "description", "country", "province",
        "municipality", "coordinates", "status", "announced_value", "currency",
        "estimated_jobs", "expected_completion", "actual_completion",
        "execution_score", "filda_edition", "source_program", "last_verified",
        "created_at", "evidence_complete", "is_externally_blocked",
        "data_completeness",
    ],
    "organizations": ["id", "name", "type", "country", "parent_org_id", "aliases",
                      "description", "created_at"],
    "project_organizations": ["id", "project_id", "organization_id", "role"],
    "events": ["id", "project_id", "event_type", "event_date", "description", "source_id"],
    "project_evidence": ["id", "project_id", "field", "value", "source_id", "observed_at"],
    "change_log": ["id", "ts", "operation", "target_table", "target_id",
                   "payload_json", "source_url", "note"],
    "db_meta": ["key", "value"],
}

# Columns that are REAL/INTEGER; empty string -> NULL for these so type
# constraints and aggregations behave correctly.
NUMERIC_COLUMNS = {
    "projects": {"announced_value", "estimated_jobs", "execution_score",
                 "evidence_complete", "is_externally_blocked"},
    "project_organizations": {"id"},
    "events": {"id", "source_id"},
    "sources": {"id"},
    "project_evidence": {"id", "source_id"},
    "change_log": {"id"},
}


def clean(row, table):
    """Return values in COLUMNS order, mapping empty strings to None where appropriate."""
    cols = COLUMNS[table]
    numeric = NUMERIC_COLUMNS.get(table, set())
    out = []
    for c in cols:
        val = row.get(c, "")
        if val is None or val == "":
            # Keep empty strings as NULL for numeric columns and FK-ish fields;
            # text fields stay NULL too (NULL is the natural "unknown" here).
            val = None
        elif c in numeric:
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    val = None
        out.append(val)
    return out


def load_csv(table):
    """Read a CSV file and return a list of cleaned row tuples.

    Validates the on-disk header against COLUMNS (exact order) and every data
    row's field count against the header, so a reordered/renamed column or a
    row with the wrong number of fields fails fast instead of silently
    mis-mapping values. The 2026-08 filda_edition bug was exactly this class:
    a manually appended row missing the empty filda_edition column shifted
    every later value left by one, and the score-consistency gate only caught
    it downstream (5 projects scored 0). This check surfaces it at the source.
    """
    path = os.path.join(DATA_dir, f"{table}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing data file: {path}")
    expected = COLUMNS[table]
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != expected:
            raise ValueError(
                f"{table}.csv header mismatch:\n"
                f"  expected {len(expected)} cols: {expected}\n"
                f"  found    {len(header) if header is not None else 0} cols: {header}")
        for lineno, row in enumerate(reader, start=2):
            if not row:
                continue  # blank line (DictReader also skips these)
            if len(row) != len(expected):
                raise ValueError(
                    f"{table}.csv line {lineno}: {len(row)} fields, expected "
                    f"{len(expected)} — a missing column shifts every later "
                    f"value left by one (see the filda_edition bug).")
            rows.append(tuple(clean(dict(zip(expected, row)), table)))
    return rows


def apply_schema(conn):
    with open(SCHEMA_path, encoding="utf-8") as f:
        conn.executescript(f.read())


def has_uncheckpointed_mutations(conn):
    """True if the live DB has change_log mutations newer than the last CSV
    checkpoint (db_meta.last_exported_at) — i.e. a rebuild from CSV would
    SILENTLY LOSE them. Counts only MUTATION_OPS; the load-seed / export-csv
    checkpoint markers this script and export_csv.py write are NOT mutations
    and must not trip the guard (else every fresh rebuild would refuse the
    next one). Returns False for a pre-guard DB (no change_log / db_meta
    tables) — there is nothing to lose. Extracted from main() so the guard is
    unit-testable against an in-memory fixture (F19 pattern)."""
    try:
        max_cl = conn.execute(
            "SELECT max(ts) FROM change_log WHERE operation IN "
            f"({','.join('?' * len(MUTATION_OPS))})",
            MUTATION_OPS).fetchone()[0]
        le = conn.execute(
            "SELECT value FROM db_meta WHERE key='last_exported_at'").fetchone()
    except sqlite3.OperationalError:
        # Pre-layer DB (no change_log/db_meta tables yet) — nothing to guard.
        return False
    last_export = le[0] if le else None
    return bool(max_cl and (not last_export or max_cl > last_export))


def insert_rows(conn, table, rows):
    cols = COLUMNS[table]
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, rows)


def main():
    parser = argparse.ArgumentParser(description="Rebuild the SQLite database from CSVs")
    parser.add_argument("--dry", action="store_true", help="Validate and count without writing")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if the DB has uncheckpointed change_log mutations "
                             "(discards them — normally run db/export_csv.py --apply first)")
    args = parser.parse_args()

    # Read all CSVs first so a malformed file fails before we touch the DB.
    all_rows = {}
    for table in TABLES:
        all_rows[table] = load_csv(table)
        print(f"  read {len(all_rows[table]):>4} rows from {table}.csv")

    if args.dry:
        print("(dry run — database not modified)")
        return

    # --- Pre-flight staleness guard ---
    # load.py rebuilds the DB from CSV checkpoints, deleting the file first.
    # If db/update.py has appended mutations to the live DB that haven't been
    # checkpointed back to CSV (via db/export_csv.py), a rebuild would SILENTLY
    # LOSE them. Refuse unless --force is passed. The guard counts only
    # MUTATION operations (MUTATION_OPS — add-*/set-status/relink-*/reverify),
    # not the load-seed / export-csv checkpoint markers this script and
    # export_csv.py write. The set is imported from db/constants.py so it can
    # never drift from the ops db/update.py actually writes (it once omitted
    # relink-*, which would have let an uncheckpointed relink be lost on rebuild).
    if os.path.exists(DB_path) and not args.force:
        conn_old = sqlite3.connect(DB_path)
        try:
            stale = has_uncheckpointed_mutations(conn_old)
        finally:
            conn_old.close()
        if stale:
            print("[REFUSE] DB has change_log mutations newer than the last CSV "
                  "checkpoint. Rebuilding now would SILENTLY LOSE them. Run "
                  "`python db/export_csv.py --apply` first, or pass --force to "
                  "discard them.")
            sys.exit(1)

    # The database is fully reproducible from CSVs, so recreate it fresh on
    # every load. CREATE TABLE IF NOT EXISTS won't add columns to an existing
    # table, so a schema.sql change (new columns/tables) requires a clean
    # rebuild — deleting the file guarantees schema.sql is applied exactly.
    import time as _time
    for suffix in ("", "-wal", "-shm"):
        p = DB_path + suffix
        if os.path.exists(p):
            # On Windows, another process briefly holding the file (IDE language
            # server, a just-closed connection) can make os.remove fail with
            # PermissionError. Retry a few times — the lock is usually transient.
            for _attempt in range(5):
                try:
                    os.remove(p)
                    break
                except PermissionError:
                    if _attempt == 4:
                        raise
                    _time.sleep(0.2)

    conn = sqlite3.connect(DB_path)
    failed = False
    try:
        apply_schema(conn)
        # schema.sql sets PRAGMA foreign_keys = ON; disable for the bulk
        # delete/insert because organizations.parent_org_id is self-referential
        # and parents may be loaded after their children in CSV order.
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
            insert_rows(conn, table, all_rows[table])
            # Reset autoincrement sequence so future manual inserts don't collide.
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = ?", (table,)
            )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

        # --- Integrity gate 1: referential integrity ---
        # FKs are off during the bulk insert, so violations are silent until
        # we check here. The original CSV bugs (bogus source_id, dangling
        # parent_org_id) were exactly this class; this gate surfaces them on
        # every rebuild instead of letting them load quietly.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            failed = True
            print("\n[FAIL] Foreign-key violations detected:")
            print("  (table, rowid, parent_table, fkid)")
            for v in violations:
                print(f"  {v}")
            print("\nFix the offending CSV row(s) and re-run.")
        else:
            print("  foreign_key_check: OK (0 violations)")

        # --- Integrity gate 2: score consistency ---
        # execution_score is loaded from projects.csv (a snapshot). Recompute
        # it from the loaded events/status/evidence and assert equality, so a
        # stale snapshot can never silently ship. If data changed, refresh the
        # snapshot with `python db/calculate_scores.py --update-csv`.
        if not failed:
            score_by_id = compute_scores(conn)
            loaded = {pid: score for pid, score in
                      conn.execute("SELECT id, execution_score FROM projects")}
            drift = [(pid, loaded.get(pid), score_by_id[pid][0])
                     for pid in score_by_id if loaded.get(pid) != score_by_id[pid][0]]
            if drift:
                failed = True
                print("\n[FAIL] execution_score in projects.csv is stale vs the formula:")
                print(f"  {'project_id':<42}{'csv':>6}{'computed':>10}")
                for pid, csv_s, comp_s in drift:
                    print(f"  {pid:<42}{str(csv_s):>6}{comp_s:>10}")
                print("\nRun `python db/calculate_scores.py --update-csv`, then re-run load.py.")
            else:
                print(f"  score consistency: OK ({len(score_by_id)} projects match the formula)")

                # Seed the checkpoint watermark if this is a fresh setup (no
                # db_meta rows in the CSV checkpoint yet).
                n_meta = conn.execute("SELECT COUNT(*) FROM db_meta").fetchone()[0]
                if n_meta == 0:
                    conn.execute(
                        "INSERT INTO db_meta (key, value) VALUES ('last_exported_at', NULL)")
                    conn.commit()

                # Formula-version watermark (see docs/scoring-methodology.md §
                # Versioning). INSERT OR IGNORE so a row carried in db_meta.csv
                # round-trips; a value mismatch with the current SCORE_VERSION
                # warns here. The score-consistency gate above is the real
                # backstop — a weight change without a snapshot refresh fails it.
                conn.execute(
                    "INSERT OR IGNORE INTO db_meta (key, value) VALUES ('score_version', ?)",
                    (SCORE_VERSION,))
                sv = conn.execute(
                    "SELECT value FROM db_meta WHERE key='score_version'").fetchone()
                if sv and sv[0] != SCORE_VERSION:
                    print(f"  [WARN] db_meta.score_version={sv[0]!r} but "
                          f"calculate_scores.SCORE_VERSION={SCORE_VERSION!r} — "
                          f"scores were snapshotted under a different formula version.")
                conn.commit()

                # Mark the seed boundary in the audit trail. This is a checkpoint
                # marker, NOT a mutation, so the staleness guard ignores it.
                import json
                counts = {t: len(all_rows[t]) for t in TABLES}
                conn.execute(
                    "INSERT INTO change_log (operation, target_table, target_id, "
                    "payload_json, source_url, note) VALUES "
                    "('load-seed', 'db_meta', NULL, ?, NULL, 'fresh rebuild from CSV')",
                    (json.dumps({"tables": counts}),))
                conn.commit()

        # --- Integrity gate 3: data_completeness consistency ---
        # data_completeness is loaded from projects.csv (a snapshot). Recompute
        # it from the loaded events and assert equality, so a stale snapshot can
        # never silently ship (mirrors the execution_score gate above).
        if not failed:
            dc_drift = []
            for pid, stored in conn.execute(
                    "SELECT id, data_completeness FROM projects"):
                types = {e[0] for e in conn.execute(
                    "SELECT event_type FROM events WHERE project_id = ?", (pid,))}
                computed = data_completeness(types)
                if stored != computed:
                    dc_drift.append((pid, stored, computed))
            if dc_drift:
                failed = True
                print("\n[FAIL] data_completeness in projects.csv is stale vs the events:")
                print(f"  {'project_id':<42}{'csv':>18}{'computed':>18}")
                for pid, csv_dc, comp_dc in dc_drift:
                    print(f"  {pid:<42}{str(csv_dc):>18}{comp_dc:>18}")
                print("\nRecompute the column from events and re-run load.py.")
            else:
                n_proj = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                print(f"  data_completeness consistency: OK ({n_proj} projects match the events)")
    finally:
        conn.close()

    if failed:
        sys.exit(1)

    print(f"\n[OK] Rebuilt {os.path.relpath(DB_path, BASE_dir)}")
    print("  Scores are formula-verified. Run `python db/calculate_scores.py` for the report.")


if __name__ == "__main__":
    main()