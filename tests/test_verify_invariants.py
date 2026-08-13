#!/usr/bin/env python3
"""
Unit tests for the structural-invariant verifier (db/verify_invariants.py).

F19 — "who watches the watchmen": the verifiers had no self-tests, so an
inverted predicate (actual != expected, a negated guard, a wrong column) was
invisible until it false-greened or false-reded against live data. These tests
pin BOTH directions against an in-memory fixture:

  - no false-red: a clean fixture (every invariant satisfied) -> 0 failures,
    0 warnings.
  - no false-green: one deliberately-introduced violation -> the SPECIFIC
    check that should catch it is recorded as failed.

The verifier's core logic lives in run_checks(conn) (extracted from main() for
testability); these tests call it directly on an in-memory schema, no DB file.
"""

import os
import sys
import sqlite3
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import verify_invariants as vi  # noqa: E402
from calculate_scores import SCORE_VERSION  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")

# Must match the case_studies list inside verify_invariants.run_checks.
CASE_STUDIES = [
    "huatong-angola-industry-awards", "linha-verde-investor-visas",
    "pt-ao-credit-line-2-5b", "pt-ao-credit-line-3-25b", "chicomba-water-dam",
    "investment-portal-georeferenced", "etu-energias-leao-ouro-2025",
]


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def make_clean_fixture():
    """A fixture that satisfies EVERY invariant: 0 failures, 0 warnings."""
    conn = make_conn()
    conn.execute(
        "INSERT INTO sources (id, url, confidence) VALUES (1, ?, 'high')",
        ("https://example.test/s1",))
    for pid in CASE_STUDIES:
        conn.execute(
            "INSERT INTO projects (id, title, status, sector, execution_score, "
            "evidence_complete, source_program, filda_edition, "
            "data_completeness) VALUES (?, ?, 'operational', 'Energy', 50, 1, "
            "'FILDA', '2024', 'partial')",
            (pid, pid))
        # A progress event that is also source-linked (satisfies both the
        # "operational has progress" warning and the "scored has a source-linked
        # event" check).
        conn.execute(
            "INSERT INTO events (project_id, event_type, event_date, source_id) "
            "VALUES (?, 'construction', '2024-01-15', 1)", (pid,))
        # Field-level evidence with a controlled-vocabulary field.
        conn.execute(
            "INSERT INTO project_evidence (project_id, field, source_id) "
            "VALUES (?, 'status', NULL)", (pid,))
    # Chicomba groundbreaking (event id 104) — the dated invariant the verifier
    # pins. Explicit id so it matches the hardcoded "event 104" anchor.
    conn.execute(
        "INSERT INTO events (id, project_id, event_type, event_date, source_id) "
        "VALUES (104, 'chicomba-water-dam', 'groundbreaking', '2026-06-13', 1)")
    conn.execute(
        "INSERT INTO db_meta (key, value) VALUES ('last_exported_at', "
        "'2026-08-13 00:00:00')")
    conn.execute(
        "INSERT INTO db_meta (key, value) VALUES ('score_version', ?)",
        (SCORE_VERSION,))
    conn.commit()
    return conn


def _failed_labels(checks):
    return [c[0] for c in checks if not c[3]]


class CleanFixtureTests(unittest.TestCase):
    def test_clean_fixture_passes_no_false_red(self):
        conn = make_clean_fixture()
        checks, warnings = vi.run_checks(conn)
        self.assertEqual(_failed_labels(checks), [], _failed_labels(checks))
        self.assertEqual(warnings, [], warnings)
        conn.close()


class DriftDetectionTests(unittest.TestCase):
    """Each test introduces ONE violation and asserts the specific check that
    should catch it is failed — proving the predicate is not inverted/short-
    circuited (no false-green).
    """

    def _failed_matching(self, conn, substr):
        checks, _ = vi.run_checks(conn)
        return [c[0] for c in checks if not c[3] and substr in c[0]]

    def test_detects_score_version_mismatch(self):
        conn = make_clean_fixture()
        conn.execute(
            "UPDATE db_meta SET value='v0-bad' WHERE key='score_version'")
        self.assertTrue(self._failed_matching(conn, "score_version"),
                        "score_version drift not caught")

    def test_detects_completion_is_award(self):
        conn = make_clean_fixture()
        conn.execute(
            "INSERT INTO events (project_id, event_type, description) "
            "VALUES (?, 'completion', 'won the AIPEX award')",
            ("huatong-angola-industry-awards",))
        self.assertTrue(self._failed_matching(conn, "completion event"),
                        "award-completion not caught")

    def test_detects_completed_without_progress(self):
        conn = make_clean_fixture()
        pid = "huatong-angola-industry-awards"
        # Remove the progress (construction) event; leave only an announcement.
        conn.execute("DELETE FROM events WHERE project_id=? AND event_type='construction'", (pid,))
        conn.execute(
            "INSERT INTO events (project_id, event_type) VALUES (?, 'announcement')",
            (pid,))
        conn.execute("UPDATE projects SET status='completed' WHERE id=?", (pid,))
        self.assertTrue(
            self._failed_matching(conn, "supported by a progress event"),
            "completed-without-progress not caught")

    def test_detects_bad_source_program(self):
        conn = make_clean_fixture()
        conn.execute(
            "UPDATE projects SET source_program='BOGUS' WHERE id=?",
            ("huatong-angola-industry-awards",))
        self.assertTrue(self._failed_matching(conn, "source_program"),
                        "bad source_program not caught")

    def test_detects_bad_evidence_field(self):
        conn = make_clean_fixture()
        conn.execute(
            "INSERT INTO project_evidence (project_id, field) VALUES (?, 'bogus_field')",
            ("huatong-angola-industry-awards",))
        self.assertTrue(self._failed_matching(conn, "evidence field"),
                        "bad evidence field not caught")

    def test_detects_data_completeness_mismatch(self):
        conn = make_clean_fixture()
        # Claim 'full' but the project has no completion event (only construction)
        # -> computed is 'partial'.
        conn.execute(
            "UPDATE projects SET data_completeness='full' WHERE id=?",
            ("huatong-angola-industry-awards",))
        self.assertTrue(self._failed_matching(conn, "data_completeness matches events"),
                        "data_completeness mismatch not caught")

    def test_detects_chicomba_date_drift(self):
        conn = make_clean_fixture()
        conn.execute(
            "UPDATE events SET event_date='2099-01-01' WHERE id=104")
        self.assertTrue(self._failed_matching(conn, "Chicomba groundbreaking"),
                        "Chicomba date drift not caught")

    def test_detects_scored_project_without_source_linked_event(self):
        """A scored project (evidence_complete=1, score>0) whose events are all
        source-less must be flagged — the score is only meaningful if a click-
        through source backs at least one event. This is the drift the
        'scored project ... has a source-linked event' check exists to catch."""
        conn = make_clean_fixture()
        conn.execute(
            "INSERT INTO projects (id, title, status, sector, execution_score, "
            "evidence_complete, source_program, data_completeness) "
            "VALUES ('p-unsourced', 'Unsourced', 'operational', 'Energy', 60, "
            "1, 'FILDA', 'partial')")
        # A progress event (so 'operational without progress' stays just a
        # warning, not a failure) but with NO source_id -> unsourced.
        conn.execute(
            "INSERT INTO events (project_id, event_type, event_date, source_id) "
            "VALUES ('p-unsourced', 'construction', '2024-02-01', NULL)")
        conn.commit()
        self.assertTrue(self._failed_matching(conn, "source-linked event"),
                        "scored-but-unsourced project not caught")


if __name__ == "__main__":
    unittest.main(verbosity=2)