#!/usr/bin/env python3
"""
Article ↔ DB contract verifier for the Angola Investment Execution Database.

Pins every figure published in articles/01-o-que-aconteceu-filda-pt.md and
articles/01-what-happened-filda-en.md to a concrete query against the database.
If the DB drifts from the published claims (a data edit that moves a score, a
sector average, a count, or the Chicomba date), this script fails loudly — the
article-vs-DB reconciliation that was done by hand on 2026-07-25 should never
need to be done by hand again.

Run after `python db/load.py` (and `python db/calculate_scores.py` if data
changed). Exits 0 if all checks pass, 1 if any fail.

    python db/verify.py
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_scores import SCORE_VERSION  # formula-version stamp (methodology § Versioning)
from constants import MUTATION_OPS, ALLOWED_OPS  # op vocab (single source of truth)

DB_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "investment_tracker.db")

# Tolerance for one-decimal averages (absorbs SQLite-vs-Python rounding-mode
# differences at the .x5 boundary, e.g. Technology 71.25 -> 71.3).
AVG_TOL = 0.05


def main():
    if not os.path.exists(DB_path):
        print(f"Database not found at {DB_path}. Run `python db/load.py` first.")
        sys.exit(2)

    conn = sqlite3.connect(DB_path)
    conn.row_factory = sqlite3.Row

    checks = []  # (label, actual, expected, passed)
    warnings = []  # strings — surfaced every run but not hard failures

    def check(label, actual, expected, tol=None):
        if tol is not None:
            ok = abs(actual - expected) <= tol
        else:
            ok = actual == expected
        checks.append((label, actual, expected, ok))

    # ---- Counts ----
    n_projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    check("project count (tracked)", n_projects, 51)

    n_scored = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE evidence_complete = 1"
    ).fetchone()[0]
    check("scored project count (evidence_complete=1)", n_scored, 50)

    n_unscored = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE evidence_complete = 0"
    ).fetchone()[0]
    check("unscored project count (no click-through evidence)", n_unscored, 1)

    unscored_ids = sorted(r[0] for r in conn.execute(
        "SELECT id FROM projects WHERE evidence_complete = 0"))
    check("unscored project is Banco Sol", unscored_ids,
          ["banco-sol-mc-empresas-2025"])

    n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    check("source count", n_sources, 130)

    n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    check("event count", n_events, 105)

    n_linked = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NOT NULL"
    ).fetchone()[0]
    check("events linked to a source", n_linked, 104)

    n_null = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NULL"
    ).fetchone()[0]
    check("events with NULL source_id", n_null, 1)

    null_ids = [r[0] for r in conn.execute(
        "SELECT id FROM events WHERE source_id IS NULL ORDER BY id")]
    check("NULL source event is event 80 (Banco Sol)", null_ids, [80])

    # ---- Aggregate score figures (SCORED projects only: evidence_complete = 1) ----
    # Tracked-but-unscored projects (no click-through evidence) are kept in the
    # DB but never move the published average — the goal's "don't score without
    # evidence" rule. See data-lineage.md "Event 80 (Banco Sol)".
    avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects WHERE evidence_complete = 1"
    ).fetchone()[0]
    check("average execution score (DB precise, scored only)",
          round(avg, 4), 62.4, tol=0.05)
    check("average execution score (article '62')", int(round(avg)), 62)

    # Distribution buckets — must match the published 10/1/7/20/12 table (50 scored).
    buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for (s,) in conn.execute(
        "SELECT execution_score FROM projects WHERE evidence_complete = 1"
    ):
        if s <= 20:
            buckets["0-20"] += 1
        elif s <= 40:
            buckets["21-40"] += 1
        elif s <= 60:
            buckets["41-60"] += 1
        elif s <= 80:
            buckets["61-80"] += 1
        else:
            buckets["81-100"] += 1
    expected_dist = {"0-20": 10, "21-40": 1, "41-60": 7, "61-80": 20, "81-100": 12}
    for bucket, count in buckets.items():
        check(f"distribution {bucket}", count, expected_dist[bucket])

    # ---- Sector averages (PT article sector table; scored projects only) ----
    # (sector, expected_count, expected_avg_one_decimal)
    expected_sectors = [
        ("Manufacturing", 4, 85.0),
        ("Digital", 1, 83.0),
        ("Trade", 3, 77.3),
        ("Logistics", 1, 77.0),
        ("Energy", 7, 73.1),
        ("Telecom", 4, 72.8),
        ("Infrastructure", 3, 72.3),
        ("Technology", 4, 71.3),
        ("Multi-sector", 8, 58.0),
        ("Government", 3, 54.3),
        ("Finance", 6, 44.0),
        ("Agriculture", 5, 36.8),
        ("Education", 1, 8.0),
    ]
    sector_rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT sector, COUNT(*), ROUND(AVG(execution_score), 1) "
        "FROM projects WHERE sector IS NOT NULL AND evidence_complete = 1 "
        "GROUP BY sector")}
    for sector, exp_n, exp_avg in expected_sectors:
        actual = sector_rows.get(sector)
        if actual is None:
            check(f"sector '{sector}' present", False, True)
        else:
            check(f"sector '{sector}' count", actual[0], exp_n)
            check(f"sector '{sector}' avg", actual[1], exp_avg, tol=AVG_TOL)

    # ---- Private vs government (scored projects only; PT article: 62,9 vs 54,3) ----
    gov = [r[0] for r in conn.execute(
        "SELECT execution_score FROM projects "
        "WHERE sector = 'Government' AND evidence_complete = 1")]
    priv = [r[0] for r in conn.execute(
        "SELECT execution_score FROM projects "
        "WHERE sector != 'Government' AND evidence_complete = 1")]
    check("government project count (scored)", len(gov), 3)
    check("private project count (scored)", len(priv), 47)
    check("government avg score", round(sum(gov) / len(gov), 1), 54.3, tol=AVG_TOL)
    check("private avg score", round(sum(priv) / len(priv), 1), 62.9, tol=AVG_TOL)

    # ---- Case-study scores (published in both articles) ----
    case_studies = {
        "huatong-angola-industry-awards": 85,      # Huatong — both articles
        "linha-verde-investor-visas": 3,           # Linha Verde — both articles
        "pt-ao-credit-line-2-5b": 70,             # Credit line 2.5B — both articles
        "pt-ao-credit-line-3-25b": 53,            # Credit line 3.25B successor
        "chicomba-water-dam": 57,                  # Chicomba — PT article
        "investment-portal-georeferenced": 83,     # Portal — both articles
        "etu-energias-leao-ouro-2025": 80,         # ETU — EN article
    }
    for pid, expected in case_studies.items():
        row = conn.execute(
            "SELECT execution_score FROM projects WHERE id = ?", (pid,)
        ).fetchone()
        actual = row[0] if row else None
        check(f"case study {pid}", actual, expected)

    # ---- Status supported by an event (auditor-style, low false positive) ----
    # A project claiming tangible execution (completed / under_construction)
    # must have at least one tangible-execution event (completion, construction,
    # groundbreaking, financing). Deliberately excludes 'operational' because
    # many non-physical projects (trade, finance, MoU) are legitimately
    # operational without a build event — flagging them would be a false
    # positive, which erodes trust more than it builds it.
    #
    # KNOWN_STATUS_ISSUES: projects whose status is plausibly correct in the
    # real world but not yet backed by a construction/groundbreaking/financing
    # source *in this DB*. Surfaced as a warning every run (needs a source)
    # rather than silently downgraded — per the "flag, don't silently fix"
    # discipline. cabinda-refinery-aipex-2026: the Cabinda refinery is a real
    # under-construction Sonangol project, but our only source is the AIPEX
    # award (an 'expansion' event), so construction status is unproven here.
    KNOWN_STATUS_ISSUES = {"cabinda-refinery-aipex-2026"}
    progress_evts = {"completion", "construction", "groundbreaking", "financing"}
    for r in conn.execute(
        "SELECT id, status FROM projects "
        "WHERE status IN ('completed', 'under_construction') "
        "AND evidence_complete = 1"
    ):
        evts = {e[0] for e in conn.execute(
            "SELECT event_type FROM events WHERE project_id = ?", (r[0],))}
        supported = bool(evts & progress_evts)
        if supported:
            check(f"status '{r[1]}' supported by a progress event ({r[0]})",
                  True, True)
        elif r[0] in KNOWN_STATUS_ISSUES:
            warnings.append(
                f"status '{r[1]}' not yet backed by a construction/"
                f"groundbreaking/financing event (KNOWN OPEN ISSUE): {r[0]} — "
                f"needs a construction source; see data-lineage.md")
        else:
            check(f"status '{r[1]}' supported by a progress event ({r[0]})",
                  False, True)

    # ---- Case-study field-level evidence (project_evidence table) ----
    # The goal: "don't score projects unless someone can click through the
    # evidence." Each published case study must have at least one row in
    # project_evidence backing a field to a source.
    for pid in case_studies:
        n = conn.execute(
            "SELECT COUNT(*) FROM project_evidence WHERE project_id = ?", (pid,)
        ).fetchone()[0]
        check(f"case study {pid} has field-level evidence", n > 0, True)

    # ---- Every scored project must have ≥1 source-linked event ----
    # Operationalises the goal's "no score without click-through evidence."
    # Banco Sol (the previous known open issue) is now evidence_complete = 0
    # (tracked but unscored), so it is excluded from scoring by design rather
    # than carried as a scored-but-unsourced exception. Any *scored* project
    # that lacks a source-linked event is therefore a hard failure.
    unsourced_scored = []
    for r in conn.execute(
        "SELECT id FROM projects WHERE execution_score > 0 AND evidence_complete = 1"
    ):
        n_linked = conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id = ? AND source_id IS NOT NULL",
            (r[0],)).fetchone()[0]
        if n_linked == 0:
            unsourced_scored.append(r[0])
    for pid in unsourced_scored:
        check(f"scored project {pid} has a source-linked event", False, True)

    # ---- Chicomba groundbreaking date (corrected 2026-06-13 per Angop) ----
    row = conn.execute(
        "SELECT event_date FROM events WHERE id = 104"
    ).fetchone()
    check("Chicomba groundbreaking (event 104) date", row[0] if row else None,
          "2026-06-13")

    # ---- Incremental-layer integrity (change_log + db_meta) ----
    # These checks guard the append-only update layer (db/update.py +
    # db/export_csv.py) and the formula-version stamp. They are additive: none of
    # the article-figure checks above are affected. The hard "uncheckpointed
    # mutations" guard lives in load.py; here we only verify the audit trail is
    # internally consistent.
    def _table_exists(name):
        try:
            conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            return True
        except sqlite3.OperationalError:
            return False

    check("change_log table present", _table_exists("change_log"), True)
    check("db_meta table present", _table_exists("db_meta"), True)

    if _table_exists("db_meta"):
        n_le = conn.execute(
            "SELECT COUNT(*) FROM db_meta WHERE key='last_exported_at'"
        ).fetchone()[0]
        check("db_meta has one last_exported_at row", n_le, 1)

        # Formula-version stamp (methodology § Versioning). The DB row is seeded
        # by load.py; a mismatch means the loaded scores were computed under a
        # different formula version than the current code — a stale-snapshot
        # signal the score-consistency gate in load.py also guards.
        sv = conn.execute(
            "SELECT value FROM db_meta WHERE key='score_version'"
        ).fetchone()
        check("db_meta has a score_version row", sv is not None, True)
        if sv is not None:
            check("db_meta score_version matches calculate_scores.SCORE_VERSION",
                  sv[0], SCORE_VERSION)

    if _table_exists("change_log"):
        # No orphan targets: every logged mutation must point at a real row.
        # target_id is TEXT so it matches both TEXT and INTEGER PKs via cast.
        orphan_checks = [
            ("add-event", "events", "events.id"),
            ("add-evidence", "project_evidence", "project_evidence.id"),
            ("add-source", "sources", "sources.id"),
            ("set-status", "projects", "projects.id"),
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
            check(f"change_log {op} rows have a matching {tbl} row",
                  orphans, 0)

        bad_ops = conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE operation NOT IN "
            f"({','.join('?' * len(ALLOWED_OPS))})",
            tuple(ALLOWED_OPS)).fetchone()[0]
        check("change_log operations all in allowed set", bad_ops, 0)

        # Watermark vs latest mutation. A lag is a WARNING (the DB is current
        # even if the CSV checkpoint lags); load.py enforces the hard guard.
        # Op set is imported from db/constants.py so it stays in sync with the
        # ops db/update.py actually writes (once omitted relink-* here too).
        max_mut = conn.execute(
            "SELECT max(ts) FROM change_log WHERE operation IN "
            f"({','.join('?' * len(MUTATION_OPS))})",
            MUTATION_OPS).fetchone()[0]
        le = conn.execute(
            "SELECT value FROM db_meta WHERE key='last_exported_at'"
        ).fetchone() if _table_exists("db_meta") else None
        last_export = le[0] if le else None
        if max_mut and (not last_export or max_mut > last_export):
            warnings.append(
                f"db_meta.last_exported_at ({last_export}) is older than the "
                f"latest change_log mutation ({max_mut}) — run "
                f"`python db/export_csv.py --apply` to checkpoint before "
                f"rebuilding with load.py")

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
        print("\n[FAIL] Article figures do not match the DB. Either fix the data "
              "and re-run, or update the published figures + this script's "
              "expectations together.")
        sys.exit(1)
    print("\n[OK] All article figures match the database.")


if __name__ == "__main__":
    main()