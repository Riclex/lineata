#!/usr/bin/env python3
"""
Unit tests for load.py's pre-flight staleness guard (H5).

load.py rebuilds the DB from CSV checkpoints, deleting the file first. If
db/update.py has appended mutations to the live DB that haven't been
checkpointed back to CSV (db/export_csv.py --apply), a rebuild would SILENTLY
LOSE them. The guard refuses unless --force is passed.

The guard logic is extracted into `has_uncheckpointed_mutations(conn)` so it is
unit-testable against an in-memory fixture (F19 pattern) without rebuilding the
whole DB. These tests pin the contract: only MUTATION operations count, the
load-seed/export-csv checkpoint markers do NOT, and the watermark comparison is
"mutation newer than last_exported_at".
"""

import os
import sys
import sqlite3
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import load  # noqa: E402
from constants import MUTATION_OPS  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")


def make_conn():
    conn = sqlite3.connect(":memory:")
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _cl(conn, ts, op):
    conn.execute(
        "INSERT INTO change_log (ts, operation, target_table, target_id, "
        "payload_json, source_url, note) VALUES (?, ?, 'projects', 'p1', NULL, NULL, NULL)",
        (ts, op))


def _watermark(conn, ts):
    if ts is None:
        conn.execute(
            "INSERT INTO db_meta (key, value) VALUES ('last_exported_at', NULL)")
    else:
        conn.execute(
            "INSERT INTO db_meta (key, value) VALUES ('last_exported_at', ?)", (ts,))


class StalenessGuardTests(unittest.TestCase):
    def _mut_op(self):
        # Pick any real mutation op the guard counts.
        return next(iter(MUTATION_OPS))

    def test_clean_checkpoint_no_mutations_allowed(self):
        """A DB with only a checkpoint marker and a watermark -> rebuild OK."""
        conn = make_conn()
        _cl(conn, "2026-08-09 00:00:00", "load-seed")  # marker, not a mutation
        _watermark(conn, "2026-08-10 00:00:00")
        self.assertFalse(load.has_uncheckpointed_mutations(conn))

    def test_mutation_newer_than_watermark_refused(self):
        conn = make_conn()
        _watermark(conn, "2026-08-10 00:00:00")
        _cl(conn, "2026-08-12 10:00:00", self._mut_op())  # newer mutation
        self.assertTrue(load.has_uncheckpointed_mutations(conn))

    def test_mutation_older_than_watermark_allowed(self):
        conn = make_conn()
        _watermark(conn, "2026-08-10 00:00:00")
        _cl(conn, "2026-08-09 00:00:00", self._mut_op())  # already checkpointed
        self.assertFalse(load.has_uncheckpointed_mutations(conn))

    def test_mutation_with_no_watermark_refused(self):
        """No last_exported_at at all means nothing was ever checkpointed, so
        any mutation is uncommitted."""
        conn = make_conn()
        _cl(conn, "2026-08-12 10:00:00", self._mut_op())
        self.assertTrue(load.has_uncheckpointed_mutations(conn))

    def test_checkpoint_marker_newer_than_watermark_does_not_trigger(self):
        """A load-seed/export-csv marker is a checkpoint, not a mutation —
        even if newer than the watermark it must not trip the guard (else every
        fresh rebuild would refuse the next one)."""
        conn = make_conn()
        _watermark(conn, "2026-08-10 00:00:00")
        _cl(conn, "2099-01-01 00:00:00", "load-seed")  # marker, far newer
        self.assertFalse(load.has_uncheckpointed_mutations(conn))

    def test_pre_guard_db_no_tables_allowed(self):
        """A DB from before the change_log/db_meta layer has nothing to guard."""
        conn = sqlite3.connect(":memory:")  # no schema loaded
        self.assertFalse(load.has_uncheckpointed_mutations(conn))


if __name__ == "__main__":
    unittest.main(verbosity=2)