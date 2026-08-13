#!/usr/bin/env python3
"""
Unit tests for the API server's query plumbing (app/server.py + db/query.py).

Pins the two things that silently broke the live API:
  - _ns() must produce every attribute build_where() reads. A missing
    source_program made /api/projects and /api/summary 500 for every request,
    and the app silently fell back to static data.json (which is why it went
    unnoticed — the fallback masked the failure).
  - dataset_stats() returns the global sidebar figures in the shape the app
    consumes (tracked/scored/sources/events/avg_score/editions), shared by
    /api/summary's 'dataset' key and app/data.json's 'stats'.

Run:
    python tests/test_server.py
    python -m unittest discover tests
"""

import os
import sys
import sqlite3
import unittest

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, DB_DIR)
import server  # noqa: E402
import query as q  # noqa: E402

SCHEMA_PATH = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


class NsTests(unittest.TestCase):
    def test_ns_covers_every_build_where_attribute(self):
        """_ns() must expose every attribute build_where() reads — a missing
        source_program made every /api/projects + /api/summary request 500."""
        ns = server._ns({})
        for attr in ("include_unscored", "sector", "province", "status",
                     "edition", "source_program", "min_score", "max_score",
                     "org", "search"):
            self.assertTrue(hasattr(ns, attr), f"_ns missing {attr!r}")

    def test_source_program_filter_applies(self):
        where, params, _ = q.build_where(server._ns({"source_program": "FILDA"}))
        self.assertIn("source_program = ?", where)
        self.assertEqual(params, ["FILDA"])


class DatasetStatsTests(unittest.TestCase):
    def test_dataset_stats_shape(self):
        conn = make_conn()
        conn.execute(
            "INSERT INTO projects (id, title, status, evidence_complete, "
            "execution_score, filda_edition) VALUES "
            "('a','A','announced',1,15,'2022'),"
            "('b','B','completed',1,70,'2026'),"
            "('c','C','announced',0,0,'2023')")
        conn.execute(
            "INSERT INTO sources (title,url,confidence) VALUES "
            "('s','https://x.test/1','high')")
        conn.execute(
            "INSERT INTO events (project_id,event_type,event_date) VALUES "
            "('a','announcement','2024-01-01')")
        conn.commit()
        s = q.dataset_stats(conn)
        self.assertEqual(s["tracked"], 3)
        self.assertEqual(s["scored"], 2)
        self.assertEqual(s["sources"], 1)
        self.assertEqual(s["events"], 1)
        self.assertEqual(s["avg_score"], 42.5)  # (15 + 70) / 2
        self.assertEqual(s["editions"], "2022–2026")


if __name__ == "__main__":
    unittest.main(verbosity=2)
