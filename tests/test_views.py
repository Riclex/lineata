"""M3: the aggregate execution views must only count/average SCORED projects
(evidence_complete = 1). An unscored project carries execution_score = 0 by
convention, not a measured zero — including it in an average silently drags the
cohort down, and counting it inflates totals with projects we don't actually
evidence-track. These tests prove each view excludes unscored projects.
"""
import os
import sys
import sqlite3
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
SCHEMA = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _project(conn, pid, score, ec, sector="Energy", province="Luanda",
             status="operational", country="Angola"):
    conn.execute(
        "INSERT INTO projects (id, title, status, sector, province, country, "
        "execution_score, evidence_complete, source_program) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'FILDA')",
        (pid, pid, status, sector, province, country, score, ec))


def _org(conn, oid, name="Acme", otype="company", country="Portugal"):
    conn.execute(
        "INSERT INTO organizations (id, name, type, country) VALUES (?, ?, ?, ?)",
        (oid, name, otype, country))


def _link(conn, pid, oid, role="promoter"):
    conn.execute(
        "INSERT INTO project_organizations (project_id, organization_id, role) "
        "VALUES (?, ?, ?)", (pid, oid, role))


class ExecutionByOrgViewTests(unittest.TestCase):
    def test_unscored_project_excluded_from_avg_and_count(self):
        conn = make_conn()
        _org(conn, "acme", country="Portugal")
        _project(conn, "p1", score=80, ec=1)   # scored
        _project(conn, "p2", score=0, ec=0)    # unscored -> must be excluded
        _link(conn, "p1", "acme")
        _link(conn, "p2", "acme")
        row = conn.execute("SELECT * FROM v_execution_by_org").fetchone()
        # Only the scored project counts: total 1, avg 80.0 (NOT 40.0).
        self.assertEqual(row["total_projects"], 1)
        self.assertEqual(row["avg_execution_score"], 80.0)


class ExecutionBySectorViewTests(unittest.TestCase):
    def test_unscored_project_excluded_from_sector_count(self):
        conn = make_conn()
        _project(conn, "p1", score=80, ec=1, sector="Energy")
        _project(conn, "p2", score=0, ec=0, sector="Energy")  # unscored
        row = conn.execute(
            "SELECT * FROM v_execution_by_sector WHERE sector='Energy'").fetchone()
        self.assertEqual(row["total_projects"], 1)


class ExecutionByProvinceViewTests(unittest.TestCase):
    def test_unscored_project_excluded_from_province_count(self):
        conn = make_conn()
        _project(conn, "p1", score=80, ec=1, province="Luanda")
        _project(conn, "p2", score=0, ec=0, province="Luanda")  # unscored
        row = conn.execute(
            "SELECT * FROM v_execution_by_province WHERE province='Luanda'").fetchone()
        self.assertEqual(row["total_projects"], 1)


class ExecutionByCountryViewTests(unittest.TestCase):
    def test_unscored_project_excluded_from_country_count(self):
        conn = make_conn()
        _org(conn, "acme", country="Portugal")
        _project(conn, "p1", score=80, ec=1)
        _project(conn, "p2", score=0, ec=0)  # unscored
        _link(conn, "p1", "acme")
        _link(conn, "p2", "acme")
        row = conn.execute(
            "SELECT * FROM v_execution_by_country WHERE investor_country='Portugal'").fetchone()
        self.assertEqual(row["total_projects"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)