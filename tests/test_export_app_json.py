#!/usr/bin/env python3
"""
Unit tests for db/export_app_json.py — the DB→static-fallback exporter.

L11: the static fallback is served when the live API is unavailable. When the
app is opened as file://, browsers block fetch('data.json'), so the fallback
must also be loadable via a <script> tag. export_app_json.py therefore writes a
second file, app/data.js, whose body is `window.__STATIC_DATA = <same json>;\n`
— valid JS a <script src="data.js"> can execute. --check must guard BOTH files,
or a stale data.js silently serves a frozen dataset on file:// with no CI signal.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
import export_app_json as ex  # noqa: E402

SCHEMA = os.path.join(DB_DIR, "schema.sql")


def _build_db(path):
    """A minimal on-disk DB the read-only exporter can open via mode=ro."""
    conn = sqlite3.connect(path)
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute(
        "INSERT INTO projects (id, title, status, sector, province, "
        "execution_score, evidence_complete, source_program) "
        "VALUES ('p1', 'Test Project', 'operational', 'Energy', 'Luanda', 50, 1, 'FILDA')")
    conn.execute(
        "INSERT INTO sources (id, url, confidence) VALUES (1, 'https://example.test/s', 'high')")
    conn.execute(
        "INSERT INTO events (id, project_id, event_type, event_date, source_id) "
        "VALUES (1, 'p1', 'construction', '2024-01-01', 1)")
    conn.commit()
    conn.close()


class DataJsBodyTests(unittest.TestCase):
    """data_js_body wraps a JSON string into a <script>-loadable JS assignment."""

    def test_wraps_json_in_window_assignment(self):
        body = ex.data_js_body('{"a": 1}')
        self.assertTrue(body.startswith("window.__STATIC_DATA = "))
        self.assertTrue(body.endswith(";\n"))

    def test_payload_parses_equal_to_input(self):
        payload = '{"tracked": 1, "scored": 1}'
        body = ex.data_js_body(payload)
        # Strip the `window.__STATIC_DATA = ` prefix and the `;\n` suffix.
        extracted = body[len("window.__STATIC_DATA = "):-2]
        self.assertEqual(json.loads(extracted), json.loads(payload))


class MainWritesBothFilesTests(unittest.TestCase):
    """main() must write both data.json and data.js from the same payload."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = os.path.join(self._tmp.name, "test.db")
        _build_db(self._db)
        self._json = os.path.join(self._tmp.name, "data.json")
        self._js = os.path.join(self._tmp.name, "data.js")
        self._orig_db = ex.DB_path
        self._orig_out = ex.OUT_path
        self._orig_js = getattr(ex, "OUT_JS_path", None)
        ex.DB_path = self._db
        ex.OUT_path = self._json
        # OUT_JS_path is added by the L11 change; tolerate its absence in RED.
        if hasattr(ex, "OUT_JS_path"):
            ex.OUT_JS_path = self._js

    def tearDown(self):
        ex.DB_path = self._orig_db
        ex.OUT_path = self._orig_out
        if self._orig_js is not None:
            ex.OUT_JS_path = self._orig_js
        self._tmp.cleanup()

    def test_main_writes_data_json_and_data_js(self):
        ex.main([])
        self.assertTrue(os.path.exists(self._json))
        self.assertTrue(os.path.exists(self._js),
                        "main() must also write app/data.js for the file:// fallback")
        with open(self._js, encoding="utf-8") as f:
            js = f.read()
        self.assertTrue(js.startswith("window.__STATIC_DATA = "),
                        "data.js must be a <script>-loadable JS assignment")
        # The JS payload must be byte-identical (same JSON) to data.json.
        with open(self._json, encoding="utf-8") as f:
            json_text = f.read()
        extracted = js[len("window.__STATIC_DATA = "):-2]
        self.assertEqual(json.loads(extracted), json.loads(json_text))

    def test_check_passes_when_both_in_sync(self):
        ex.main([])  # write both
        ex.main(["--check"])  # must not exit non-zero

    def test_check_exits_1_when_data_js_stale(self):
        ex.main([])  # write both in sync
        # Corrupt data.js so it no longer matches the DB payload.
        with open(self._js, "w", encoding="utf-8") as f:
            f.write("window.__STATIC_DATA = {\"stale\": true};\n")
        with self.assertRaises(SystemExit) as cm:
            ex.main(["--check"])
        self.assertEqual(cm.exception.code, 1)

    def test_check_exits_1_when_data_json_stale(self):
        ex.main([])  # write both in sync
        with open(self._json, "w", encoding="utf-8") as f:
            f.write('{"stale": true}')
        with self.assertRaises(SystemExit) as cm:
            ex.main(["--check"])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)