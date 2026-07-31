#!/usr/bin/env python3
"""
Unit tests for the execution-score formula (db/calculate_scores.py).

These tests pin the *formula* — the part of the system most likely to drift
silently and the part the published figures depend on. They build an in-memory
SQLite database from db/schema.sql, seed a minimal project + events, and call
the real `calculate_score` so the tests exercise the same code path that
db/load.py's score-consistency gate and db/export_csv.py use.

Run:
    python tests/test_calculate_score.py           # direct
    python -m unittest tests.test_calculate_score  # via the unittest runner
    python -m unittest discover tests              # discover all tests

Stdlib only — no pytest or third-party deps, matching the rest of the codebase.
Each test isolates one component of the formula (base / events / evidence /
delay / status penalty / only-announce / clamp) by asserting the specific
breakdown value, so a regression points straight at the term that moved.
"""

import os
import sys
import sqlite3
import unittest

# Make db/ importable and locate the schema.
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
from calculate_scores import calculate_score, SCORE_VERSION  # noqa: E402

SCHEMA_PATH = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    """Fresh in-memory DB with the full schema loaded and FKs on."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def add_project(conn, pid, status="announced", evidence_complete=1,
                jobs=None, actual_completion=None):
    conn.execute(
        "INSERT INTO projects (id, title, status, estimated_jobs, "
        "actual_completion, evidence_complete) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, pid, status, jobs, actual_completion, evidence_complete))
    conn.commit()


_source_counter = [0]


def add_source(conn, confidence="medium"):
    """Insert a source and return its id. Confidence defaults to medium.

    The URL is made unique per call via a counter so multiple sources with the
    same confidence can coexist (sources.url has a UNIQUE index on non-empty
    values). The brief's helper produced a constant URL per confidence, which
    collided when a test needed two medium-confidence sources.
    """
    _source_counter[0] += 1
    n = _source_counter[0]
    cur = conn.execute(
        "INSERT INTO sources (title, url, confidence) VALUES (?, ?, ?)",
        (f"src-{confidence}-{n}", f"https://example.test/{confidence}/{n}", confidence))
    return cur.lastrowid


def add_event(conn, pid, event_type, date="2024-01-01", source_id=None):
    conn.execute(
        "INSERT INTO events (project_id, event_type, event_date, source_id) "
        "VALUES (?, ?, ?, ?)", (pid, event_type, date, source_id))
    conn.commit()


def add_evidence(conn, pid, field, source_id, observed_at="2024-01-01"):
    conn.execute(
        "INSERT INTO project_evidence (project_id, field, value, source_id, observed_at) "
        "VALUES (?, ?, ?, ?, ?)", (pid, field, "v", source_id, observed_at))
    conn.commit()


def score(conn, pid):
    """Compute (score, breakdown) for a project via the real formula."""
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return calculate_score(conn, project)


class UnscoredTests(unittest.TestCase):
    def test_evidence_complete_zero_is_unscored_and_excluded(self):
        """evidence_complete=0 → score 0, breakdown flagged unscored
        (tracked but not scored — the Banco Sol rule). The published
        averages/distribution exclude these by filtering evidence_complete=1."""
        conn = make_conn()
        add_project(conn, "p", status="operational", evidence_complete=0,
                    jobs=500, actual_completion="2026")
        add_event(conn, "p", "completion", "2026-01-01")
        s, b = score(conn, "p")
        self.assertEqual(s, 0)
        self.assertTrue(b["unscored"])


class BaseAndOnlyAnnounceTests(unittest.TestCase):
    def test_pure_announcement_gets_only_announcement_penalty(self):
        """A single 'announcement' event and nothing else → -10 only-announce.
        Score = base(announced 15) + events(3) - 10 = 8."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        add_event(conn, "p", "announcement", "2024-01-01")
        s, b = score(conn, "p")
        self.assertEqual(b["base"], 15)
        self.assertEqual(b["events"], 3)
        self.assertEqual(b["only_announce"], -10)
        self.assertEqual(s, 8)

    def test_any_non_announcement_event_clears_only_announce(self):
        """One follow-up event of any kind removes the only-announce penalty."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "mou", "2024-06-01")
        s, b = score(conn, "p")
        self.assertEqual(b["only_announce"], 0)


class EventsCapTests(unittest.TestCase):
    def test_event_points_count_distinct_types_not_raw_count(self):
        """v2: points sum by DISTINCT event type, not raw event count.
        Three `completion` events = ONE distinct type = +15 (not +45)."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        for d in ("2024-01-01", "2024-06-01", "2025-01-01"):
            add_event(conn, "p", "completion", d)
        _, b = score(conn, "p")
        self.assertEqual(b["events"], 15)

    def test_event_points_capped_at_30_with_distinct_types(self):
        """v2: distinct types reaching the cap: completion(15)+expansion(10)+
        groundbreaking(8)+financing(7) = 40 -> capped at 30."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        for t in ("completion", "expansion", "groundbreaking", "financing"):
            add_event(conn, "p", t, "2024-01-01")
        _, b = score(conn, "p")
        self.assertEqual(b["events"], 30)

    def test_zero_point_events_do_not_inflate(self):
        """Zero-point types add 0 even as distinct types."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        add_event(conn, "p", "announcement", "2024-01-01")  # +3 (clears only-announce)
        add_event(conn, "p", "delay", "2024-06-01")        # +0
        add_event(conn, "p", "suspension", "2025-01-01")   # +0
        _, b = score(conn, "p")
        self.assertEqual(b["events"], 3)


class EvidenceBonusTests(unittest.TestCase):
    def _bonus(self, conn, pid):
        return score(conn, pid)[1]["evidence"]

    def test_jobs_signal_high_confidence_full(self):
        conn = make_conn()
        sid = add_source(conn, "high")
        add_project(conn, "p", status="announced", jobs=100)
        add_evidence(conn, "p", "estimated_jobs", sid)
        add_event(conn, "p", "announcement", "2024-01-01", source_id=conn.execute(
            "INSERT INTO sources (title,url,confidence) VALUES (?,?,?)",
            ("a","https://x.test/a","high")).lastrowid)
        self.assertEqual(self._bonus(conn, "p"), 3)  # 3 * 1.0

    def test_jobs_signal_medium_confidence_half(self):
        conn = make_conn()
        sid = add_source(conn, "medium")
        add_project(conn, "p", status="announced", jobs=100)
        add_evidence(conn, "p", "estimated_jobs", sid)
        add_event(conn, "p", "announcement", "2024-01-01",
                  source_id=add_source(conn, "medium"))
        self.assertEqual(self._bonus(conn, "p"), 2)  # int(3*0.5 + 0.5) = 2

    def test_jobs_signal_low_confidence_zero(self):
        conn = make_conn()
        sid = add_source(conn, "low")
        add_project(conn, "p", status="announced", jobs=100)
        add_evidence(conn, "p", "estimated_jobs", sid)
        add_event(conn, "p", "announcement", "2024-01-01",
                  source_id=add_source(conn, "medium"))
        self.assertEqual(self._bonus(conn, "p"), 0)  # 3 * 0.0

    def test_production_signal_uses_max_confidence(self):
        # one high + one medium completion event -> max = high -> 2*1.0 = 2
        conn = make_conn()
        add_project(conn, "p", status="under_construction")
        sh = add_source(conn, "high"); sm = add_source(conn, "medium")
        add_event(conn, "p", "announcement", "2024-01-01", source_id=sm)
        add_event(conn, "p", "completion", "2024-06-01", source_id=sm)
        add_event(conn, "p", "completion", "2025-01-01", source_id=sh)
        self.assertEqual(self._bonus(conn, "p"), 2)

    def test_signal_no_source_zero(self):
        # production event with NULL source -> 0
        conn = make_conn()
        add_project(conn, "p", status="under_construction")
        add_event(conn, "p", "announcement", "2024-01-01",
                  source_id=add_source(conn, "medium"))
        add_event(conn, "p", "completion", "2024-06-01", source_id=None)
        self.assertEqual(self._bonus(conn, "p"), 0)

    def test_evidence_bonus_capped_at_10(self):
        conn = make_conn()
        sh = add_source(conn, "high")
        add_project(conn, "p", status="completed", jobs=500, actual_completion="2026")
        add_evidence(conn, "p", "estimated_jobs", sh)
        add_evidence(conn, "p", "actual_completion", sh)
        add_event(conn, "p", "announcement", "2024-01-01", source_id=sh)
        add_event(conn, "p", "completion", "2026-01-01", source_id=sh)
        add_event(conn, "p", "expansion", "2026-06-01", source_id=sh)
        self.assertEqual(self._bonus(conn, "p"), 10)


class DelayPenaltyTests(unittest.TestCase):
    def _delay(self, conn, pid):
        return score(conn, pid)[1]["delay"]

    def test_under_one_year_no_penalty(self):
        conn = make_conn()
        add_project(conn, "p", status="operational", actual_completion="2024-06-01")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2024-06-01")
        self.assertEqual(self._delay(conn, "p"), 0)

    def test_one_to_two_years_minus_5(self):
        conn = make_conn()
        add_project(conn, "p", status="operational", actual_completion="2025-06-01")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2025-06-01")
        self.assertEqual(self._delay(conn, "p"), -5)

    def test_two_to_three_years_minus_10(self):
        conn = make_conn()
        add_project(conn, "p", status="operational", actual_completion="2026-06-01")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2026-06-01")
        self.assertEqual(self._delay(conn, "p"), -10)

    def test_three_plus_years_minus_15(self):
        conn = make_conn()
        add_project(conn, "p", status="operational", actual_completion="2026-01-01")
        add_event(conn, "p", "announcement", "2022-01-01")
        add_event(conn, "p", "completion", "2026-01-01")
        self.assertEqual(self._delay(conn, "p"), -15)


class StatusPenaltyTests(unittest.TestCase):
    def _status_pen(self, conn, pid):
        return score(conn, pid)[1]["status_penalty"]

    def test_delayed_status_penalty(self):
        conn = make_conn()
        add_project(conn, "p", status="delayed")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2024-06-01")
        self.assertEqual(self._status_pen(conn, "p"), -10)

    def test_suspended_status_penalty(self):
        conn = make_conn()
        add_project(conn, "p", status="suspended")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2024-06-01")
        self.assertEqual(self._status_pen(conn, "p"), -15)

    def test_unknown_status_penalty(self):
        conn = make_conn()
        add_project(conn, "p", status="unknown")
        add_event(conn, "p", "announcement", "2024-01-01")
        add_event(conn, "p", "completion", "2024-06-01")
        self.assertEqual(self._status_pen(conn, "p"), -10)


class ClampAndVersionTests(unittest.TestCase):
    def test_score_clamped_to_100(self):
        """completed(70) + capped events(30) + capped evidence(10) = 110 → 100.
        v2: evidence signals backed by high-confidence sources so the
        confidence-weighted bonus stays 10."""
        conn = make_conn()
        sh = add_source(conn, "high")
        add_project(conn, "p", status="completed",
                    jobs=500, actual_completion="2026")
        add_evidence(conn, "p", "estimated_jobs", sh)
        add_evidence(conn, "p", "actual_completion", sh)
        add_event(conn, "p", "announcement", "2024-01-01", source_id=sh)
        add_event(conn, "p", "completion", "2026-01-01", source_id=sh)
        add_event(conn, "p", "expansion", "2026-06-01", source_id=sh)
        add_event(conn, "p", "groundbreaking", "2026-04-01", source_id=sh)
        s, _ = score(conn, "p")
        self.assertEqual(s, 100)

    def test_score_clamped_to_0(self):
        """cancelled(base 0) + only-announce(-10) = -10 → clamped to 0."""
        conn = make_conn()
        add_project(conn, "p", status="cancelled")
        add_event(conn, "p", "announcement", "2024-01-01")
        s, _ = score(conn, "p")
        self.assertEqual(s, 0)

    def test_breakdown_carries_score_version(self):
        """Every breakdown stamps the formula version (methodology § Versioning)."""
        conn = make_conn()
        add_project(conn, "p", status="announced")
        add_event(conn, "p", "announcement", "2024-01-01")
        _, b = score(conn, "p")
        self.assertEqual(b["version"], SCORE_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)