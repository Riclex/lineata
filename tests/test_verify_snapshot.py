import json
import os
import sys
import sqlite3
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


class SnapshotTests(unittest.TestCase):
    def test_generate_then_compare_round_trips(self):
        conn = make_conn()
        conn.execute(
            "INSERT INTO projects (id, title, status, sector, execution_score, "
            "evidence_complete) VALUES (?, ?, ?, ?, ?, ?)",
            ("p1", "P1", "operational", "Energy", 60, 1))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)