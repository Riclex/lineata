#!/usr/bin/env python3
"""
Unit tests for load.py's structural-invariant gate (L5).

load.py historically ran only FK + score-consistency + data_completeness gates.
A bad source_program or evidence field (caught by verify_invariants) would load
silently and only surface if someone ran health.py. L5 adds gate 4: load.py runs
the structural invariants and fails loud at build time. The gate is extracted
into `run_invariant_gate(conn) -> (failed, messages)` so it is unit-testable
against an in-memory fixture without a full CSV rebuild (same pattern as
has_uncheckpointed_mutations).
"""

import os
import sys
import sqlite3
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import load  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _clean_fixture():
    """A fixture satisfying every structural invariant (mirrors
    test_verify_invariants.make_clean_fixture, kept local to avoid cross-file
    test imports). 0 failures, 0 warnings under run_checks."""
    conn = make_conn()
    conn.execute(
        "INSERT INTO sources (id, url, confidence) VALUES (1, ?, 'high')",
        ("https://example.test/s1",))
    case_studies = [
        "huatong-angola-industry-awards", "linha-verde-investor-visas",
        "pt-ao-credit-line-2-5b", "pt-ao-credit-line-3-25b", "chicomba-water-dam",
        "investment-portal-georeferenced", "etu-energias-leao-ouro-2025",
    ]
    for pid in case_studies:
        conn.execute(
            "INSERT INTO projects (id, title, status, sector, execution_score, "
            "evidence_complete, source_program, filda_edition, "
            "data_completeness) VALUES (?, ?, 'operational', 'Energy', 50, 1, "
            "'FILDA', '2024', 'partial')",
            (pid, pid))
        conn.execute(
            "INSERT INTO events (project_id, event_type, event_date, source_id) "
            "VALUES (?, 'construction', '2024-01-15', 1)", (pid,))
        conn.execute(
            "INSERT INTO project_evidence (project_id, field, source_id) "
            "VALUES (?, 'status', NULL)", (pid,))
    conn.execute(
        "INSERT INTO events (id, project_id, event_type, event_date, source_id) "
        "VALUES (104, 'chicomba-water-dam', 'groundbreaking', '2026-06-13', 1)")
    conn.execute(
        "INSERT INTO db_meta (key, value) VALUES ('last_exported_at', "
        "'2026-08-13 00:00:00')")
    from calculate_scores import SCORE_VERSION
    conn.execute(
        "INSERT INTO db_meta (key, value) VALUES ('score_version', ?)",
        (SCORE_VERSION,))
    conn.commit()
    return conn


class InvariantGateTests(unittest.TestCase):
    def test_clean_fixture_passes_gate(self):
        """A clean fixture must NOT false-fail the gate (load.py must not refuse
        a valid rebuild)."""
        conn = _clean_fixture()
        failed, messages = load.run_invariant_gate(conn)
        self.assertFalse(failed, f"clean fixture falsely failed: {messages}")

    def test_bogus_source_program_fails_gate(self):
        """A project with a source_program outside constants.SOURCE_PROGRAMS
        must fail the gate — this is the exact silent-load bug L5 fixes (it
        loaded fine under the old FK/score/data_completeness gates)."""
        conn = _clean_fixture()
        conn.execute(
            "UPDATE projects SET source_program='BOGUS' WHERE id=?",
            ("huatong-angola-industry-awards",))
        conn.commit()
        failed, messages = load.run_invariant_gate(conn)
        self.assertTrue(failed, "bogus source_program did not fail the gate")
        self.assertTrue(any("source_program" in m for m in messages),
                        f"no message mentions source_program: {messages}")


if __name__ == "__main__":
    unittest.main(verbosity=2)