import json
import os
import sys
import sqlite3
import tempfile
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
SCHEMA = os.path.join(DB_DIR, "schema.sql")
sys.path.insert(0, os.path.dirname(DB_DIR))  # for db package import
import importlib.util
spec = importlib.util.spec_from_file_location(
    "verify_snapshot", os.path.join(DB_DIR, "verify_snapshot.py"))
vs = importlib.util.module_from_spec(spec); spec.loader.exec_module(vs)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _add_project(conn, pid, score=60, sector="Energy", ec=1,
                 source_program="FILDA"):
    conn.execute(
        "INSERT INTO projects (id, title, status, sector, execution_score, "
        "evidence_complete, source_program) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, pid, "operational", sector, score, ec, source_program))


class SnapshotTests(unittest.TestCase):
    def test_generate_then_compare_round_trips(self):
        conn = make_conn()
        _add_project(conn, "p1", score=60)
        conn.execute(
            "INSERT INTO projects (id, title, status, execution_score, evidence_complete) "
            "VALUES (?, ?, ?, ?, ?)", ("p2", "P2", "announced", 0, 0))
        snap = vs.generate_snapshot(conn)
        self.assertEqual(snap["counts"]["projects"], 2)
        self.assertEqual(snap["counts"]["scored"], 1)
        self.assertEqual(snap["counts"]["unscored"], 1)
        # compare against the same snapshot -> no drift
        drift = vs.compare_snapshot(conn, snap)
        self.assertEqual(drift, [])


class CompareSnapshotDriftTests(unittest.TestCase):
    """F4: prove compare_snapshot actually catches drift. The round-trip test
    only compares a snapshot to itself (drift == []); if compare_snapshot were
    broken to always return [], it would still pass. These tests mutate the DB
    AFTER generating the baseline and assert a named drift appears — so an
    inverted or short-circuited comparator is caught.
    """

    def test_detects_count_drift(self):
        conn = make_conn()
        _add_project(conn, "p1", score=60)
        snap = vs.generate_snapshot(conn)
        _add_project(conn, "p2", score=50)
        drift = vs.compare_snapshot(conn, snap)
        self.assertTrue(any("counts.projects" in d for d in drift), drift)

    def test_detects_score_drift(self):
        conn = make_conn()
        # A case-study id so the case_study field is pinned and drifted.
        _add_project(conn, "huatong-angola-industry-awards", score=60)
        snap = vs.generate_snapshot(conn)
        conn.execute(
            "UPDATE projects SET execution_score=70 WHERE id=?",
            ("huatong-angola-industry-awards",))
        drift = vs.compare_snapshot(conn, snap)
        self.assertTrue(any("case_study" in d for d in drift), drift)

    def test_detects_distribution_drift(self):
        conn = make_conn()
        _add_project(conn, "p1", score=60)  # bucket 41-60
        snap = vs.generate_snapshot(conn)
        # Move the score across a bucket boundary (no count change).
        conn.execute("UPDATE projects SET execution_score=10 WHERE id=?", ("p1",))
        drift = vs.compare_snapshot(conn, snap)
        self.assertTrue(any("distribution." in d for d in drift), drift)


class ArticlePinTests(unittest.TestCase):
    """F4: check_articles is never tested in the existing suite. Pin both
    directions — a missing avg figure is flagged, a present one is accepted.
    """

    def setUp(self):
        self._orig_articles_dir = vs.ARTICLES_dir
        self.tmp = tempfile.mkdtemp()
        vs.ARTICLES_dir = self.tmp
        # check_articles scans for specific filenames; create one EN article.
        self.article_path = os.path.join(self.tmp, "01-what-happened-filda-en.md")

    def tearDown(self):
        vs.ARTICLES_dir = self._orig_articles_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conn_with_filda_avg(self, score):
        conn = make_conn()
        _add_project(conn, "p1", score=score, source_program="FILDA")
        return conn

    def test_flags_missing_avg(self):
        conn = self._conn_with_filda_avg(60)  # filda avg -> rounded 60
        with open(self.article_path, "w", encoding="utf-8") as f:
            f.write("An article with no headline number at all.\n")
        drift = vs.check_articles(conn)
        self.assertTrue(any("avg score" in d for d in drift), drift)

    def test_passes_when_avg_present(self):
        conn = self._conn_with_filda_avg(60)
        with open(self.article_path, "w", encoding="utf-8") as f:
            f.write("The average execution score is 60 across the cohort.\n")
        drift = vs.check_articles(conn)
        self.assertEqual(drift, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)