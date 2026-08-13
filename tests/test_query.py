#!/usr/bin/env python3
"""
Unit tests for the read-only query layer (db/query.py).

Pins the three behaviors that, if silently broken, make the API and CLI return
wrong data with no error:
  - build_where() excludes unscored projects by default (the published figures
    are scored-only); --include-unscored opens it up. A regression that dropped
    the evidence_complete=1 clause would silently inflate every count/average.
  - query_projects() honors that default at the listing level.
  - project_detail() returns None for an unknown id (not an exception), and for
    a known id returns the score breakdown, timeline, and the per-project
    changelog pulled from change_log by project_id in the payload.

All tests run against an in-memory schema; no DB file.
"""

import json
import os
import sys
import sqlite3
import unittest
from argparse import Namespace

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import query as q  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _ns(**kw):
    base = dict(include_unscored=False, sector=None, province=None, status=None,
                edition=None, source_program=None, min_score=None, max_score=None,
                org=None, search=None)
    base.update(kw)
    return Namespace(**base)


def _project(conn, pid, score=50, ec=1, sector="Energy", province="Luanda",
             status="operational", source_program="FILDA"):
    conn.execute(
        "INSERT INTO projects (id, title, status, sector, province, "
        "execution_score, evidence_complete, source_program) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, pid, status, sector, province, score, ec, source_program))


class BuildWhereTests(unittest.TestCase):
    def test_excludes_unscored_by_default(self):
        where, params, _ = q.build_where(_ns())
        self.assertIn("evidence_complete = 1", where)

    def test_include_unscored_drops_evidence_clause(self):
        where, params, _ = q.build_where(_ns(include_unscored=True))
        self.assertNotIn("evidence_complete", where)

    def test_score_bounds_applied(self):
        where, params, _ = q.build_where(_ns(min_score=40, max_score=80))
        self.assertIn("execution_score >= ?", where)
        self.assertIn("execution_score <= ?", where)
        self.assertEqual(params, [40, 80])

    def test_sector_and_province_filters(self):
        where, params, _ = q.build_where(_ns(sector="Energy", province="Luanda"))
        self.assertIn("p.sector = ?", where)
        self.assertIn("p.province = ?", where)
        self.assertEqual(params, ["Energy", "Luanda"])


class QueryProjectsTests(unittest.TestCase):
    def test_returns_scored_only_by_default(self):
        conn = make_conn()
        _project(conn, "p1", score=50, ec=1)
        _project(conn, "p2", score=0, ec=0)  # unscored, excluded
        out = q.query_projects(conn, _ns())
        ids = [p["id"] for p in out]
        self.assertEqual(ids, ["p1"])

    def test_include_unscored_returns_all(self):
        conn = make_conn()
        _project(conn, "p1", score=50, ec=1)
        _project(conn, "p2", score=0, ec=0)
        out = q.query_projects(conn, _ns(include_unscored=True))
        self.assertEqual(sorted(p["id"] for p in out), ["p1", "p2"])

    def test_min_score_filter(self):
        conn = make_conn()
        _project(conn, "lo", score=20, ec=1)
        _project(conn, "hi", score=80, ec=1)
        out = q.query_projects(conn, _ns(min_score=50))
        self.assertEqual([p["id"] for p in out], ["hi"])


class ProjectDetailTests(unittest.TestCase):
    def test_unknown_id_returns_none(self):
        conn = make_conn()
        self.assertIsNone(q.project_detail(conn, "no-such-project"))

    def test_known_id_returns_breakdown_events_and_changelog(self):
        conn = make_conn()
        _project(conn, "p1", score=50, ec=1)
        conn.execute(
            "INSERT INTO events (id, project_id, event_type, event_date, source_id) "
            "VALUES (1, 'p1', 'construction', '2024-01-01', NULL)")
        # A change_log row whose payload embeds this project_id (the O(n) scan).
        conn.execute(
            "INSERT INTO change_log (ts, operation, target_table, target_id, "
            "payload_json, source_url, note) VALUES "
            "('2024-01-02 00:00:00', 'set-status', 'projects', 'p1', ?, NULL, NULL)",
            (json.dumps({"project_id": "p1", "old": "announced", "new": "operational"}),))
        # A change_log row for a DIFFERENT project — must not leak in.
        conn.execute(
            "INSERT INTO change_log (ts, operation, target_table, target_id, "
            "payload_json, source_url, note) VALUES "
            "('2024-01-03 00:00:00', 'set-status', 'projects', 'p2', ?, NULL, NULL)",
            (json.dumps({"project_id": "p2"}),))
        d = q.project_detail(conn, "p1")
        self.assertIsNotNone(d)
        self.assertEqual(d["id"], "p1")
        self.assertIn("score_breakdown", d)
        self.assertIsInstance(d["score_breakdown"], dict)
        self.assertEqual(len(d["events"]), 1)
        self.assertEqual(d["events"][0]["event_type"], "construction")
        # Only p1's changelog row is pulled.
        self.assertEqual(len(d["changelog"]), 1)
        self.assertEqual(d["changelog"][0]["operation"], "set-status")


if __name__ == "__main__":
    unittest.main(verbosity=2)