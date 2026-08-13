#!/usr/bin/env python3
"""
Unit tests for db/digest.py — the monthly status-change digest.

L4: gather()'s op dispatch only bucketed set-status / add-event / retype-event /
add-evidence / add-source. The other MUTATION_OPS members — set-blocked,
relink-event, relink-evidence, reverify — had no bucket and no `else`
fallthrough, so they silently vanished from the digest. And a FUTURE op added to
constants.MUTATION_OPS would disappear the same way with no warning. These tests
pin that every dropped op now appears in the digest, and that an unbucketed
MUTATION_OPS member is surfaced as a warning instead of dropping silently.
"""

import json
import os
import sys
import sqlite3
import unittest
from datetime import date, timedelta

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import digest as dg  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")

SINCE = "2026-01-01"
UNTIL = "2026-02-01"


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _log(conn, op, payload, ts="2026-01-15 12:00:00", source_url=None):
    conn.execute(
        "INSERT INTO change_log (operation, target_table, target_id, "
        "payload_json, source_url, note) VALUES (?, 'x', NULL, ?, ?, NULL)",
        (op, json.dumps(payload, ensure_ascii=False), source_url))


class DroppedOpsTests(unittest.TestCase):
    """Each dropped op must appear in the rendered digest (currently absent)."""

    def setUp(self):
        self.conn = _conn()
        self.conn.execute(
            "INSERT INTO projects (id, title, status, source_program) "
            "VALUES ('p1', 'Project One', 'delayed', 'FILDA')")
        self.conn.commit()

    def test_set_blocked_appears(self):
        _log(self.conn, "set-blocked",
             {"project_id": "p1", "blocked": True},
             source_url="https://example.test/blocked")
        by_project, warnings = dg.gather(self.conn, SINCE)
        out = dg.render(by_project, SINCE, UNTIL, warnings=warnings)
        self.assertIn("set-blocked", out)
        self.assertIn("p1", out)

    def test_relink_event_appears(self):
        _log(self.conn, "relink-event",
             {"project_id": "p1", "event_id": 5, "old_source_id": 1,
              "new_source_id": 2})
        by_project, warnings = dg.gather(self.conn, SINCE)
        out = dg.render(by_project, SINCE, UNTIL, warnings=warnings)
        self.assertIn("relink-event", out)

    def test_relink_evidence_appears(self):
        _log(self.conn, "relink-evidence",
             {"project_id": "p1", "field": "status"})
        by_project, warnings = dg.gather(self.conn, SINCE)
        out = dg.render(by_project, SINCE, UNTIL, warnings=warnings)
        self.assertIn("relink-evidence", out)

    def test_reverify_appears(self):
        _log(self.conn, "reverify",
             {"project_id": "p1", "source_id": 3})
        by_project, warnings = dg.gather(self.conn, SINCE)
        out = dg.render(by_project, SINCE, UNTIL, warnings=warnings)
        self.assertIn("reverify", out)


class FallthroughWarningTests(unittest.TestCase):
    """An op that IS in constants.MUTATION_OPS but has no digest bucket must be
    surfaced as a warning, not dropped silently — so a future op added to the
    vocabulary can't disappear unnoticed. Simulated by emptying the bucket for
    an op that otherwise IS handled."""

    def test_unbucketed_mutation_op_warns(self):
        conn = _conn()
        conn.execute(
            "INSERT INTO projects (id, title, status, source_program) "
            "VALUES ('p1', 'Project One', 'delayed', 'FILDA')")
        conn.commit()
        _log(conn, "set-blocked", {"project_id": "p1", "blocked": True})
        # Pretend set-blocked lost its bucket (as it is today, pre-fix).
        orig = dg.BLOCKED_OPS
        dg.BLOCKED_OPS = ()
        try:
            by_project, warnings = dg.gather(conn, SINCE)
        finally:
            dg.BLOCKED_OPS = orig
        self.assertTrue(any("set-blocked" in w for w in warnings),
                        f"expected a warning mentioning set-blocked, got {warnings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)