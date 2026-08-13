#!/usr/bin/env python3
"""
Unit tests for the doc-figure drift verifier (db/verify_docs.py).

F19 — "who watches the watchmen": verify_docs scans docs/*.md + README.md for
hand-cited numbers and compares them to the DB. An inverted regex or a wrong
capture group (e.g. group(2) where group(1) was meant) is invisible until it
false-greened or false-reded against the live docs. These tests pin BOTH
directions against an in-memory fixture + monkeypatched doc paths:

  - no false-green: a doc that cites a wrong number -> the SPECIFIC check that
    should catch it is recorded as failed.
  - no false-red: the same doc with the correct number -> that check passes.

The verifier's core logic lives in run_checks(conn, vchk) (extracted from
main() for testability); these tests call it directly on an in-memory schema
with stubbed doc globals, so `unittest discover` runs anywhere with no real DB
and no subprocess. Three representative checks are pinned — one per at-risk
category (a captured count, a case-study score, a free-form-prose average) —
each in both directions.
"""

import os
import sys
import sqlite3
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import verify_docs as vd  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")
VCHK = 60  # stubbed verify_invariants+verify_snapshot check count


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _add_scored(conn, pid, score, sector="Energy", edition="2024"):
    conn.execute(
        "INSERT INTO projects (id, title, status, sector, execution_score, "
        "evidence_complete, source_program, filda_edition) "
        "VALUES (?, ?, 'operational', ?, ?, 1, 'FILDA', ?)",
        (pid, pid, sector, score, edition))


class _DocsFixture(unittest.TestCase):
    """Shared setUp/tearDown: redirect vd's doc globals to temp files so
    run_checks reads only the fixture docs we write, and stub the check-count
    subprocess."""

    def setUp(self):
        import tempfile
        self._saved = {
            "DOCS": vd.DOCS, "README": vd.README, "EXTRACT_README": vd.EXTRACT_README,
            "LINEAGE": vd.LINEAGE, "SCORING": vd.SCORING,
            "verify_check_count": vd.verify_check_count,
        }
        self.tmp = tempfile.mkdtemp()
        vd.DOCS = self.tmp  # no getting-started.md inside -> that check skips
        vd.README = os.path.join(self.tmp, "README.md")
        vd.EXTRACT_README = os.path.join(self.tmp, "extract_README.md")  # absent -> skip
        vd.LINEAGE = os.path.join(self.tmp, "data-lineage.md")
        vd.SCORING = os.path.join(self.tmp, "scoring-methodology.md")
        # README and SCORING are read unconditionally; give README the anchor
        # matching VCHK and leave SCORING empty (no anchors -> those checks
        # record expected=None FAILs, which we don't assert; tests that need a
        # scoring anchor overwrite it).
        self._write(vd.README, f"verify_invariants.py ({VCHK} checks)\n")
        self._write(vd.SCORING, "")
        vd.verify_check_count = lambda: VCHK

    def tearDown(self):
        import shutil
        vd.DOCS = self._saved["DOCS"]
        vd.README = self._saved["README"]
        vd.EXTRACT_README = self._saved["EXTRACT_README"]
        vd.LINEAGE = self._saved["LINEAGE"]
        vd.SCORING = self._saved["SCORING"]
        vd.verify_check_count = self._saved["verify_check_count"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _run(self, conn):
        return vd.run_checks(conn, vchk=VCHK)

    def _ok(self, checks, substr):
        """ok flag of the first check whose label contains substr, or None."""
        for label, ok, _exp, _act, _loc in checks:
            if substr in label:
                return ok
        return None


class ScoredCountCheckTests(_DocsFixture):
    """Locks the F5 de-hardcoding: the 'over NN scored' count is captured from
    the doc and compared to the DB n_scored, not baked in as a literal."""

    def _lineage(self, n_doc):
        return (f"Average score: **50.00 over {n_doc} scored projects**\n")

    def test_correct_count_passes(self):
        conn = make_conn()
        _add_scored(conn, "p1", score=50)  # n_scored = 1, avg = 50.0
        self._write(vd.LINEAGE, self._lineage(1))
        checks = self._run(conn)
        self.assertTrue(self._ok(checks, "Average score scored count"),
                        "correct scored count flagged (false red)")

    def test_wrong_count_fails(self):
        conn = make_conn()
        _add_scored(conn, "p1", score=50)
        self._write(vd.LINEAGE, self._lineage(999))
        checks = self._run(conn)
        self.assertFalse(self._ok(checks, "Average score scored count"),
                         "wrong scored count not caught (false green)")


class InvestmentPortalScoreCheckTests(_DocsFixture):
    """Locks an F7 new check: the Article-Layer Investment Portal case-study
    score is compared to the DB execution_score for that project id."""

    def _lineage(self, score_doc):
        return f"Investment Portal is also DB-tracked at score {score_doc}\n"

    def test_correct_score_passes(self):
        conn = make_conn()
        _add_scored(conn, "investment-portal-georeferenced", score=81)
        self._write(vd.LINEAGE, self._lineage(81))
        checks = self._run(conn)
        self.assertTrue(self._ok(checks, "Investment Portal score"),
                        "correct IP score flagged (false red)")

    def test_wrong_score_fails(self):
        conn = make_conn()
        _add_scored(conn, "investment-portal-georeferenced", score=81)
        self._write(vd.LINEAGE, self._lineage(99))
        checks = self._run(conn)
        self.assertFalse(self._ok(checks, "Investment Portal score"),
                         "wrong IP score not caught (false green)")


class EditionAvgCheckTests(_DocsFixture):
    """Locks the F7 rounding-agnostic edition-avg check: the doc's 1-dp value
    is compared to the RAW DB average with a tolerance that accepts either
    nearest rounding, in both data-lineage.md and scoring-methodology.md."""

    def _lineage(self, e26):
        return f"rises by edition (2022: 33.6 → 2026: {e26})\n"

    def _scoring(self, e26):
        return f"rises steadily by edition (2022: 33.6 → 2026: {e26})\n"

    def _conn(self):
        conn = make_conn()
        _add_scored(conn, "p22", score=33.6, edition="2022")  # ed22 = 33.6
        _add_scored(conn, "p26", score=43.3, edition="2026")  # ed26 = 43.3
        return conn

    def test_correct_edition_avgs_pass(self):
        conn = self._conn()
        self._write(vd.LINEAGE, self._lineage(43.3))
        self._write(vd.SCORING, self._scoring(43.3))
        checks = self._run(conn)
        self.assertTrue(self._ok(checks, "data-lineage edition avg 2026"),
                        "lineage 2026 avg flagged (false red)")
        self.assertTrue(self._ok(checks, "data-lineage edition avg 2022"),
                        "lineage 2022 avg flagged (false red)")
        self.assertTrue(self._ok(checks, "scoring-methodology edition avg 2026"),
                        "scoring 2026 avg flagged (false red)")

    def test_wrong_edition_avg_fails(self):
        conn = self._conn()
        self._write(vd.LINEAGE, self._lineage(99.9))
        self._write(vd.SCORING, self._scoring(99.9))
        checks = self._run(conn)
        self.assertFalse(self._ok(checks, "data-lineage edition avg 2026"),
                         "lineage 2026 avg drift not caught (false green)")
        self.assertFalse(self._ok(checks, "scoring-methodology edition avg 2026"),
                         "scoring 2026 avg drift not caught (false green)")
        # The 2022 anchor is untouched in both docs -> still passes.
        self.assertTrue(self._ok(checks, "data-lineage edition avg 2022"),
                        "lineage 2022 avg wrongly flagged")


if __name__ == "__main__":
    unittest.main(verbosity=2)