#!/usr/bin/env python3
"""
Structural invariant verifier for the Angola Investment Execution Database.

The checks here hold for ANY valid dataset and never need editing when data
grows: audit-trail integrity, score-version stamp, award/completion guard,
evidence gating, and status-backed-by-progress. Hardcoded snapshot figures
(counts, averages, distribution) live in db/verify_snapshot.py instead.

Run after `python db/load.py`. Exits 0 if all pass, 1 if any fail.

    python db/verify_invariants.py
"""

import os
import sys
import sqlite3

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_scores import SCORE_VERSION
from constants import (MUTATION_OPS, ALLOWED_OPS, looks_like_award,
                       SOURCE_PROGRAMS, EVIDENCE_FIELDS, DATA_COMPLETENESS,
                       data_completeness)

DB_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "investment_tracker.db")


def run_checks(conn):
    """Run all structural-invariant checks against `conn`. Returns
    (checks, warnings): checks is a list of (label, actual, expected, ok);
    warnings is a list of human-readable warning strings. Pure read — does not
    close `conn` or exit; main() owns those. Extracted from main() so the
    verifier is unit-testable against an in-memory fixture (F19: the verifiers
    had no self-tests, so an inverted predicate was invisible until it
    false-greened/red against live data).
    """
    checks = []  # (label, actual, expected, ok)
    warnings = []

    def check(label, actual, expected, tol=None):
        if tol is not None:
            ok = abs(actual - expected) <= tol
        else:
            ok = actual == expected
        checks.append((label, actual, expected, ok))

    # ---- change_log + db_meta integrity ----
    def _exists(name):
        try:
            conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            return True
        except sqlite3.OperationalError:
            return False

    check("change_log table present", _exists("change_log"), True)
    check("db_meta table present", _exists("db_meta"), True)

    if _exists("db_meta"):
        n_le = conn.execute(
            "SELECT COUNT(*) FROM db_meta WHERE key='last_exported_at'").fetchone()[0]
        check("db_meta has one last_exported_at row", n_le, 1)
        sv = conn.execute("SELECT value FROM db_meta WHERE key='score_version'").fetchone()
        check("db_meta has a score_version row", sv is not None, True)
        if sv is not None:
            check("db_meta score_version matches calculate_scores.SCORE_VERSION",
                  sv[0], SCORE_VERSION)

    if _exists("change_log"):
        orphan_checks = [
            ("add-event", "events", "events.id"),
            ("add-evidence", "project_evidence", "project_evidence.id"),
            ("add-source", "sources", "sources.id"),
            ("set-status", "projects", "projects.id"),
            ("set-blocked", "projects", "projects.id"),
            ("relink-event", "events", "events.id"),
            ("relink-evidence", "project_evidence", "project_evidence.id"),
            ("retype-event", "events", "events.id"),
        ]
        for op, tbl, target_col in orphan_checks:
            orphans = conn.execute(
                f"SELECT COUNT(*) FROM change_log cl WHERE cl.operation=? "
                f"AND cl.target_id IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {tbl} "
                f"WHERE CAST({target_col} AS TEXT) = cl.target_id)",
                (op,)).fetchone()[0]
            check(f"change_log {op} rows have a matching {tbl} row", orphans, 0)
        bad_ops = conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE operation NOT IN "
            f"({','.join('?' * len(ALLOWED_OPS))})", tuple(ALLOWED_OPS)).fetchone()[0]
        check("change_log operations all in allowed set", bad_ops, 0)
        max_mut = conn.execute(
            "SELECT max(ts) FROM change_log WHERE operation IN "
            f"({','.join('?' * len(MUTATION_OPS))})", MUTATION_OPS).fetchone()[0]
        le = conn.execute(
            "SELECT value FROM db_meta WHERE key='last_exported_at'").fetchone()
        last_export = le[0] if le else None
        if max_mut and (not last_export or max_mut > last_export):
            warnings.append(
                f"db_meta.last_exported_at ({last_export}) is older than the latest "
                f"change_log mutation ({max_mut}) — run `python db/export_csv.py --apply` "
                f"to checkpoint before rebuilding with load.py")

    # ---- award/completion guard (Check A) ----
    award_completions = [
        r[0] for r in conn.execute(
            "SELECT id, description FROM events WHERE event_type='completion'")
        if looks_like_award(r[1])]
    check("no completion event is an award", len(award_completions), 0)
    if award_completions:
        for eid in award_completions:
            checks.append((f"completion event {eid} is an award (re-type to expansion)",
                           True, False, False))

    # ---- status backed by genuine progress (Check B hard / Check C warning) ----
    progress_evts = {"completion", "construction", "groundbreaking", "financing"}
    for r in conn.execute(
        "SELECT id, status FROM projects WHERE evidence_complete = 1"):
        evts = {e[0] for e in conn.execute(
            "SELECT event_type FROM events WHERE project_id = ?", (r[0],))}
        supported = bool(evts & progress_evts)
        if r[1] in ("completed", "under_construction"):
            if supported:
                check(f"status '{r[1]}' supported by a progress event ({r[0]})", True, True)
            else:
                check(f"status '{r[1]}' supported by a progress event ({r[0]})", False, True)
        elif r[1] == "operational" and not supported:
            warnings.append(
                f"status 'operational' has no genuine progress event "
                f"(completion/construction/groundbreaking/financing): {r[0]} — "
                f"backed only by {sorted(evts) or 'no events'}; needs operational "
                f"evidence or a status downgrade (see data-lineage.md)")

    # ---- case-study field-level evidence ----
    case_studies = [
        "huatong-angola-industry-awards", "linha-verde-investor-visas",
        "pt-ao-credit-line-2-5b", "pt-ao-credit-line-3-25b", "chicomba-water-dam",
        "investment-portal-georeferenced", "etu-energias-leao-ouro-2025",
    ]
    for pid in case_studies:
        n = conn.execute(
            "SELECT COUNT(*) FROM project_evidence WHERE project_id = ?", (pid,)).fetchone()[0]
        check(f"case study {pid} has field-level evidence", n > 0, True)

    # ---- every scored project has >=1 source-linked event ----
    unsourced_scored = []
    for r in conn.execute(
        "SELECT id FROM projects WHERE execution_score > 0 AND evidence_complete = 1"):
        n_linked = conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id = ? AND source_id IS NOT NULL",
            (r[0],)).fetchone()[0]
        if n_linked == 0:
            unsourced_scored.append(r[0])
    for pid in unsourced_scored:
        check(f"scored project {pid} has a source-linked event", False, True)

    # ---- Chicomba groundbreaking date (corrected 2026-06-13 per Angop) ----
    row = conn.execute("SELECT event_date FROM events WHERE id = 104").fetchone()
    check("Chicomba groundbreaking (event 104) date", row[0] if row else None, "2026-06-13")

    # ---- source_program in allowed set (Tier 3 coverage expansion) ----
    bad = [(r[0], r[1]) for r in conn.execute(
        "SELECT id, source_program FROM projects") if r[1] not in SOURCE_PROGRAMS]
    for pid, sp in bad:
        check(f"source_program {sp!r} in allowed set ({pid})", False, True)
    check("all projects source_program in allowed set", len(bad), 0)

    # ---- project_evidence.field in controlled vocabulary (rec. #9) ----
    bad_fields = [(r[0], r[1]) for r in conn.execute(
        "SELECT project_id, field FROM project_evidence")
        if r[1] not in EVIDENCE_FIELDS]
    for pid, f in bad_fields:
        check(f"evidence field {f!r} in allowed set ({pid})", False, True)
    check("all project_evidence fields in allowed set", len(bad_fields), 0)

    # ---- data_completeness in allowed set + matches events (rec. #10) ----
    bad_dc = [(r[0], r[1]) for r in conn.execute(
        "SELECT id, data_completeness FROM projects")
        if r[1] not in DATA_COMPLETENESS]
    for pid, dc in bad_dc:
        check(f"data_completeness {dc!r} in allowed set ({pid})", False, True)
    check("all projects data_completeness in allowed set", len(bad_dc), 0)

    dc_drift = []
    for r in conn.execute("SELECT id, data_completeness FROM projects"):
        types = {e[0] for e in conn.execute(
            "SELECT event_type FROM events WHERE project_id = ?", (r[0],))}
        computed = data_completeness(types)
        if r[1] != computed:
            dc_drift.append((r[0], r[1], computed))
    for pid, stored, computed in dc_drift:
        check(f"data_completeness matches events ({pid})", stored, computed)
    check("all projects data_completeness matches events", len(dc_drift), 0)

    return checks, warnings


def main():
    if not os.path.exists(DB_path):
        print(f"Database not found at {DB_path}. Run `python db/load.py` first.")
        sys.exit(2)

    conn = sqlite3.connect(DB_path)
    conn.row_factory = sqlite3.Row
    checks, warnings = run_checks(conn)
    conn.close()

    # ---- Report ----
    width = 64
    print(f"{'Check':<{width}}{'Result':>8}")
    print("-" * (width + 8))
    failures = 0
    for label, actual, expected, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
            print(f"{label:<{width}}{status:>8}")
            print(f"    expected {expected!r}, got {actual!r}")
        else:
            print(f"{label:<{width}}{status:>8}")
    print("-" * (width + 8))
    print(f"{len(checks)} checks, {len(checks) - failures} passed, {failures} failed")
    if warnings:
        print(f"{len(warnings)} known open issue(s):")
        for w in warnings:
            print(f"  ! {w}")
    if failures:
        print("\n[FAIL] Invariant checks failed. Fix the data and re-run.")
        sys.exit(1)
    print("\n[OK] All structural invariants hold.")


if __name__ == "__main__":
    main()
