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


class OriginAllowedTests(unittest.TestCase):
    """H2: POST /api/leads must reject cross-origin submissions from origins
    the operator didn't allowlist. Unset allowlist = dev (allow all); a set
    allowlist is an exact-match whitelist of Origin headers."""

    def setUp(self):
        self._orig = server.LEADS_ORIGIN_ALLOWLIST
        server.LEADS_ORIGIN_ALLOWLIST = ()  # dev default: allow all

    def tearDown(self):
        server.LEADS_ORIGIN_ALLOWLIST = self._orig

    def test_unset_allowlist_allows_any_origin(self):
        self.assertTrue(server.origin_allowed("https://evil.test"))
        self.assertTrue(server.origin_allowed(None))

    def test_set_allowlist_allows_listed_rejects_others(self):
        server.LEADS_ORIGIN_ALLOWLIST = ("https://filda.test",
                                         "https://app.filda.test")
        self.assertTrue(server.origin_allowed("https://filda.test"))
        self.assertTrue(server.origin_allowed("https://app.filda.test"))
        self.assertFalse(server.origin_allowed("https://evil.test"))
        # A missing Origin header is NOT assumed safe once a allowlist is set.
        self.assertFalse(server.origin_allowed(None))


class RateLimiterTests(unittest.TestCase):
    """H2: per-IP sliding-window rate limit on lead submissions."""

    def test_allows_up_to_max_then_blocks(self):
        rl = server.RateLimiter(max_hits=3, window_seconds=60)
        self.assertTrue(rl.check("1.1.1.1", now=0))
        self.assertTrue(rl.check("1.1.1.1", now=1))
        self.assertTrue(rl.check("1.1.1.1", now=2))
        self.assertFalse(rl.check("1.1.1.1", now=3))  # 4th blocked

    def test_independent_per_ip(self):
        rl = server.RateLimiter(max_hits=1, window_seconds=60)
        self.assertTrue(rl.check("1.1.1.1", now=0))
        self.assertTrue(rl.check("2.2.2.2", now=0))  # different IP, its own bucket

    def test_window_expiry_frees_a_slot(self):
        rl = server.RateLimiter(max_hits=2, window_seconds=60)
        self.assertTrue(rl.check("1.1.1.1", now=0))
        self.assertTrue(rl.check("1.1.1.1", now=10))
        self.assertFalse(rl.check("1.1.1.1", now=20))   # 3rd within window
        self.assertTrue(rl.check("1.1.1.1", now=71))    # first hit aged out


class HandleLeadTests(unittest.TestCase):
    """H2: the pure handler behind POST /api/leads — origin check, rate limit,
    validation, capture — returns (status, body) without touching the HTTP
    machinery, so it is unit-testable (the handler wires it to the wire)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self._orig_leads = server.LEADS_path
        self._orig_allow = server.LEADS_ORIGIN_ALLOWLIST
        self._orig_rl = server.RATE_LIMITER
        server.LEADS_path = os.path.join(self.tmp, "leads.csv")
        server.LEADS_ORIGIN_ALLOWLIST = ()
        server.RATE_LIMITER = server.RateLimiter(max_hits=10, window_seconds=60)

    def tearDown(self):
        server.LEADS_path = self._orig_leads
        server.LEADS_ORIGIN_ALLOWLIST = self._orig_allow
        server.RATE_LIMITER = self._orig_rl
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_email_accepted_and_counted(self):
        status, body = server.handle_lead("a@b.com", "1.1.1.1", None)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "count": 1})

    def test_invalid_email_rejected(self):
        status, body = server.handle_lead("not-an-email", "1.1.1.1", None)
        self.assertEqual(status, 400)
        self.assertIn("email", body["error"].lower())

    def test_disallowed_origin_rejected_before_capture(self):
        server.LEADS_ORIGIN_ALLOWLIST = ("https://filda.test",)
        status, body = server.handle_lead("a@b.com", "1.1.1.1",
                                          "https://evil.test")
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(server.LEADS_path))

    def test_rate_limited_rejected_before_capture(self):
        server.RATE_LIMITER = server.RateLimiter(max_hits=1, window_seconds=60)
        server.handle_lead("a@b.com", "1.1.1.1", None)  # exhausts the 1 slot
        status, body = server.handle_lead("b@c.com", "1.1.1.1", None)
        self.assertEqual(status, 429)
        # Only the first lead was captured.
        with open(server.LEADS_path, encoding="utf-8") as f:
            self.assertEqual(sum(1 for _ in f) - 1, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
