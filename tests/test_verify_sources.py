#!/usr/bin/env python3
"""
Unit tests for the source-liveness classifier (db/verify_sources.py classify()).

These tests pin the *classification* behavior — the two bugs that caused the
2026-08-10 liveness detour, plus the stable rules around them:

  1. Some servers reject HEAD yet serve GET 200 (financesone.worldbank.org).
     A HEAD 404/401 must NOT decide the status — "alive" means what a browser
     (a GET) sees, so only the GET result may decide.
  2. Some outlets bot-flag the bare-stdlib UA and 401 a live page, while the
     realistic browser UA gets a 200. The check must report what a human sees.

Network is fully stubbed: urllib.request.urlopen is replaced with a
programmable fake, so these tests run offline and deterministically. They
exercise the same `classify` code path that db/verify_sources.py and
db/update.py's add-source/reverify use.

Run:
    python tests/test_verify_sources.py           # direct
    python -m unittest discover tests              # discover all tests

Stdlib only — no pytest or third-party deps, matching the rest of the codebase.
"""

import io
import os
import sys
import unittest
import urllib.error

# Make db/ importable.
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import verify_sources  # noqa: E402


def make_http_error(url, code, msg):
    """Build an HTTPError that is safe to raise and garbage-collect.

    HTTPError subclasses urllib.response.addbase, which embeds a
    tempfile._TemporaryFileCloser; its finalizer emits a GC ResourceWarning
    whenever the error is collected without close(). Every error these tests
    raise is caught inside classify() and never closed by the test — so close
    it up front. classify() only reads .code, so closing early is harmless and
    keeps test output clean.
    """
    err = urllib.error.HTTPError(url, code, msg, None, io.BytesIO())
    err.close()
    return err


class FakeResponse:
    """Minimal stand-in for urllib's response: status + context-manager."""

    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    """Replaceable urllib.request.urlopen.

    Calls the per-test handler with (request, method), where method is the
    normalized HTTP method ('HEAD' or 'GET'). Handlers branch on method,
    request.full_url, and request.get_header('User-agent') to simulate
    whichever server behavior a test wants to pin.
    """

    def __init__(self, handler):
        self.handler = handler

    def __call__(self, request, *args, **kwargs):
        return self.handler(request, request.get_method())


def http_error(code):
    """Handler factory: raise HTTPError(code) for every request."""
    def _raise(request, method):
        raise make_http_error(request.full_url, code, f"HTTP {code}")
    return _raise


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = verify_sources.urllib.request.urlopen

    def tearDown(self):
        verify_sources.urllib.request.urlopen = self._orig_urlopen

    def _install(self, handler):
        verify_sources.urllib.request.urlopen = FakeUrlopen(handler)

    def _browser_ua(self, request):
        return request.get_header("User-agent") == verify_sources.UA

    # --- the two 2026-08-10 regressions ---

    def test_head_404_get_200_is_alive(self):
        """HEAD 404 must not decide: a GET 200 means the page is alive."""
        def handler(request, method):
            if method == "HEAD":
                raise make_http_error(request.full_url, 404, "Not Found")
            return FakeResponse(200)
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("alive", 200))

    def test_head_403_get_200_is_alive(self):
        """Same fall-through for a HEAD 403 (blocked-looking HEAD, live GET)."""
        def handler(request, method):
            if method == "HEAD":
                raise make_http_error(request.full_url, 403, "Forbidden")
            return FakeResponse(200)
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("alive", 200))

    def test_browser_ua_sent_and_bot_401_becomes_alive(self):
        """Bot-flagged outlets 401 a bare UA but 200 the browser UA.

        The verifier must send the realistic UA (report what a human sees),
        and a 401-on-headers must not kill a page the browser UA can fetch.
        """
        def handler(request, method):
            if not self._browser_ua(request):
                raise make_http_error(request.full_url, 401, "Unauthorized")
            return FakeResponse(200)
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("alive", 200))

    # --- stable rules around the fix ---

    def test_head_200_short_circuits_alive(self):
        """A HEAD 200 still short-circuits (cheap probe, no GET issued)."""
        seen = []

        def handler(request, method):
            seen.append(method)
            return FakeResponse(200)
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("alive", 200))
        self.assertEqual(seen, ["HEAD"])  # GET never issued

    def test_400_401_403_blocked(self):
        # 400 is access-denied in practice (Facebook returns 400 to bots, 200 to
        # browsers) — "blocked" means what a human sees, not what the checker got.
        for code in (400, 401, 403):
            with self.subTest(code=code):
                self._install(http_error(code))
                self.assertEqual(
                    verify_sources.classify("https://example.test/x"),
                    ("blocked", code))

    def test_404_410_dead(self):
        for code in (404, 410):
            with self.subTest(code=code):
                self._install(http_error(code))
                self.assertEqual(
                    verify_sources.classify("https://example.test/x"),
                    ("dead", code))

    def test_other_http_errors_dead(self):
        # 429/5xx are "link not usable" per the classifier, not access-denied.
        for code in (500, 503, 429, 451):
            with self.subTest(code=code):
                self._install(http_error(code))
                self.assertEqual(
                    verify_sources.classify("https://example.test/x"),
                    ("dead", code))

    def test_connection_error_dead_without_code(self):
        def handler(request, method):
            raise urllib.error.URLError(OSError("connection refused"))
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("dead", None))

    def test_timeout_dead_without_code(self):
        def handler(request, method):
            raise TimeoutError("timed out")
        self._install(handler)
        self.assertEqual(verify_sources.classify("https://example.test/x"),
                         ("dead", None))


class ApplyAtomicityTests(unittest.TestCase):
    """H1: under --apply, a DB-write failure must leave sources.csv UNCHANGED.

    The CSV is the source of truth that survives rebuilds; the DB is a derived
    artifact. Today the code writes the CSV first (non-atomic open(...,'w')),
    then the DB — so a DB failure leaves an already-mutated CSV on disk, and a
    crash mid-CSV-write can truncate it. The fix writes the DB first (rollback
    on failure, exit before touching the CSV) and writes the CSV atomically.
    This test forces a DB failure and asserts the CSV is pristine afterward.
    """
    HEADER = "id,title,url,date,publisher,archived_url,confidence,last_verified,url_status"

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp, "sources.csv")
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(self.HEADER + "\n")
            f.write("1,Source One,https://example.test/a,2020-01-01,Pub,,high,"
                    "2020-01-01,dead\n")
            f.write("2,Publisher Only,,2020-01-01,Pub,,high,2020-01-01,n/a\n")
        # Empty DB file (no `sources` table) -> the UPDATE inside --apply fails.
        self.db_path = os.path.join(self.tmp, "empty.db")
        self._orig_csv = verify_sources.SOURCES_csv
        self._orig_db = verify_sources.DB_path
        self._orig_classify = verify_sources.classify
        self._orig_argv = sys.argv
        verify_sources.SOURCES_csv = self.csv_path
        verify_sources.DB_path = self.db_path
        verify_sources.classify = lambda url: ("alive", 200)

    def tearDown(self):
        verify_sources.SOURCES_csv = self._orig_csv
        verify_sources.DB_path = self._orig_db
        verify_sources.classify = self._orig_classify
        sys.argv = self._orig_argv
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, rid):
        import csv
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["id"] == rid:
                    return r
        return None

    def test_db_write_failure_leaves_sources_csv_unchanged(self):
        import io as _io
        import contextlib
        sys.argv = ["verify_sources.py", "--apply"]
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                verify_sources.main()
        # The DB write failed (no `sources` table) -> the URL row must still
        # carry its original stamp, NOT today/alive.
        row = self._row("1")
        self.assertIsNotNone(row)
        self.assertEqual(row["last_verified"], "2020-01-01")
        self.assertEqual(row["url_status"], "dead")

    def test_db_write_success_persists_to_csv_and_db(self):
        """GREEN path: when the DB write succeeds, both the DB and the atomic
        CSV write reflect the new stamp, and a 'reverify' audit row is logged."""
        import io as _io
        import contextlib
        import sqlite3
        from datetime import date
        # Build a real DB with the sources + change_log tables and seed it.
        schema = os.path.join(DB_DIR, "schema.sql")
        db = sqlite3.connect(self.db_path)
        with open(schema, encoding="utf-8") as f:
            db.executescript(f.read())
        db.execute("DELETE FROM sources")
        db.execute(
            "INSERT INTO sources (id, title, url, date, publisher, confidence, "
            "last_verified, url_status) VALUES (1,'Source One','https://example.test/a',"
            "'2020-01-01','Pub','high','2020-01-01','dead')")
        db.execute(
            "INSERT INTO sources (id, title, url, date, publisher, confidence, "
            "last_verified, url_status) VALUES (2,'Publisher Only','','2020-01-01',"
            "'Pub','high','2020-01-01','n/a')")
        db.commit()
        db.close()
        sys.argv = ["verify_sources.py", "--apply"]
        today = date.today().isoformat()
        with contextlib.redirect_stdout(_io.StringIO()):
            verify_sources.main()
        # CSV reflects the new stamp...
        row = self._row("1")
        self.assertEqual(row["last_verified"], today)
        self.assertEqual(row["url_status"], "alive")
        # ...and so does the DB, plus one reverify audit row.
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        src = db.execute("SELECT last_verified, url_status FROM sources WHERE id=1").fetchone()
        self.assertEqual(src["last_verified"], today)
        self.assertEqual(src["url_status"], "alive")
        n_rev = db.execute(
            "SELECT COUNT(*) FROM change_log WHERE operation='reverify' AND target_id='1'").fetchone()[0]
        db.close()
        self.assertEqual(n_rev, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
