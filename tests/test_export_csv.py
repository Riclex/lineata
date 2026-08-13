#!/usr/bin/env python3
"""
Unit tests for db/export_csv.py — the DB→CSV checkpointer.

L3: export_table() interpolates the on-disk CSV header (`header_of`) as quoted
SQL identifiers with no validation that each header column is a real DB column
(unlike load.py, which validates the CSV header against an exact COLUMNS list).
A renamed or shortened disk header yields a confusing sqlite3.OperationalError
("no such column") mid-export, or — worse — silently mis-maps if a stale header
column happens to name a column that still exists but means something else.
The fix validates the disk header against PRAGMA table_info and fails loud with
a clear ValueError before any SELECT/write.
"""

import csv
import os
import sys
import sqlite3
import tempfile
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import export_csv as ex  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


class ExportTableHeaderValidationTests(unittest.TestCase):
    """L3: export_table must reject a disk CSV header whose columns are not all
    real DB columns — fail loud at the start instead of a mid-export
    OperationalError or a silent mis-map."""

    def setUp(self):
        # Point export_csv at a temp data dir so header_of() reads our fixture
        # CSV and atomic_write_csv() never touches the real data/*.csv.
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = ex.DATA_dir
        ex.DATA_dir = self._tmp.name

    def tearDown(self):
        ex.DATA_dir = self._orig_data_dir
        self._tmp.cleanup()

    def _write_header(self, table, cols):
        path = os.path.join(self._tmp.name, f"{table}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(cols)

    def test_rejects_disk_header_with_non_db_column(self):
        conn = _conn()
        # sources exists in the schema; 'bogus_col' does not.
        self._write_header("sources", ["id", "url", "bogus_col"])
        with self.assertRaises(ValueError) as cm:
            ex.export_table(conn, "sources", {})
        self.assertIn("bogus_col", str(cm.exception))

    def test_accepts_disk_header_matching_db_columns(self):
        conn = _conn()
        # The real sources columns — must export without raising.
        self._write_header(
            "sources",
            ["id", "title", "url", "date", "publisher", "archived_url",
             "confidence", "last_verified", "url_status"])
        # export_table writes to the temp dir; just assert no raise + row count.
        conn.execute(
            "INSERT INTO sources (id, url, confidence) "
            "VALUES (1, 'https://example.test/s', 'high')")
        conn.commit()
        n = ex.export_table(conn, "sources", {})
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)