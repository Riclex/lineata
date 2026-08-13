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

    # M13-tail: the remaining build_where branches H5 did not pin. Each asserts
    # its clause + params are emitted — a regression that dropped the branch
    # (e.g. deleting the `if args.status` block) makes the clause vanish and the
    # test fail, so the filters can't silently stop filtering.

    def test_status_filter(self):
        where, params, _ = q.build_where(_ns(status="operational"))
        self.assertIn("p.status = ?", where)
        self.assertEqual(params, ["operational"])

    def test_edition_filter(self):
        where, params, _ = q.build_where(_ns(edition="2024"))
        self.assertIn("p.filda_edition = ?", where)
        self.assertEqual(params, ["2024"])

    def test_source_program_filter(self):
        where, params, _ = q.build_where(_ns(source_program="AIPEX"))
        self.assertIn("p.source_program = ?", where)
        self.assertEqual(params, ["AIPEX"])

    def test_org_filter_adds_join_and_clause(self):
        where, params, joins = q.build_where(_ns(org="Sonangol"))
        # The org filter is the only branch that adds a JOIN — if the branch is
        # dropped, joins stays "" and the name/id clause is absent.
        self.assertIn("JOIN project_organizations po", joins)
        self.assertIn("JOIN organizations o", joins)
        self.assertIn("(o.name = ? OR o.id = ?)", where)
        self.assertEqual(params, ["Sonangol", "Sonangol"])

    def test_search_filter(self):
        where, params, _ = q.build_where(_ns(search="solar"))
        self.assertIn("p.title LIKE ?", where)
        self.assertIn("p.sector LIKE ?", where)
        self.assertIn("p.subsector LIKE ?", where)
        self.assertIn("p.province LIKE ?", where)
        self.assertEqual(params, ["%solar%"] * 4)


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

    # M13-tail end-to-end: each filter actually narrows the listing, not just
    # emits a clause. A dropped branch returns every project (the filter is a
    # no-op), so the assertion on the filtered id list fails.

    def test_status_filter_end_to_end(self):
        conn = make_conn()
        _project(conn, "op", score=50, ec=1, status="operational")
        _project(conn, "ann", score=50, ec=1, status="announced")
        out = q.query_projects(conn, _ns(status="operational"))
        self.assertEqual([p["id"] for p in out], ["op"])

    def test_edition_filter_end_to_end(self):
        conn = make_conn()
        _project(conn, "e24", score=50, ec=1)
        _project(conn, "e23", score=50, ec=1)
        conn.execute("UPDATE projects SET filda_edition='2024' WHERE id='e24'")
        conn.execute("UPDATE projects SET filda_edition='2023' WHERE id='e23'")
        out = q.query_projects(conn, _ns(edition="2024"))
        self.assertEqual([p["id"] for p in out], ["e24"])

    def test_source_program_filter_end_to_end(self):
        conn = make_conn()
        _project(conn, "fildap", score=50, ec=1, source_program="FILDA")
        _project(conn, "aipexp", score=50, ec=1, source_program="AIPEX")
        out = q.query_projects(conn, _ns(source_program="AIPEX"))
        self.assertEqual([p["id"] for p in out], ["aipexp"])

    def test_org_filter_end_to_end(self):
        conn = make_conn()
        _project(conn, "linked", score=50, ec=1)
        _project(conn, "solo", score=50, ec=1)
        conn.execute(
            "INSERT INTO organizations (id, name, type, country) VALUES "
            "('sonangol', 'Sonangol', 'state_owned_enterprise', 'Angola')")
        conn.execute(
            "INSERT INTO project_organizations (project_id, organization_id, role) "
            "VALUES ('linked', 'sonangol', 'promoter')")
        out = q.query_projects(conn, _ns(org="Sonangol"))
        self.assertEqual([p["id"] for p in out], ["linked"])

    def test_search_filter_end_to_end(self):
        conn = make_conn()
        _project(conn, "solarproj", score=50, ec=1, sector="Solar")
        _project(conn, "roadproj", score=50, ec=1, sector="Transport")
        out = q.query_projects(conn, _ns(search="solar"))
        self.assertEqual([p["id"] for p in out], ["solarproj"])


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