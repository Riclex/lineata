import os
import sqlite3
import sys
import unittest

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")
sys.path.insert(0, DB_DIR)
SCHEMA = os.path.join(DB_DIR, "schema.sql")

import update
import verify_sources  # monkeypatched below


def make_temp_db(path):
    conn = sqlite3.connect(path)
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO db_meta (key, value) VALUES ('last_exported_at', NULL)")
    conn.execute("INSERT INTO projects (id, title, status, evidence_complete) "
                 "VALUES ('p1', 'P1', 'announced', 1)")
    conn.execute("INSERT INTO sources (id, title, url, confidence) "
                 "VALUES (1, 'src', 'https://example.test/ann', 'high')")
    conn.execute("INSERT INTO events (id, project_id, event_type, event_date, source_id) "
                 "VALUES (1, 'p1', 'announcement', '2024-01-01', 1)")
    # Compute the initial score so the DB starts in a consistent state.
    from calculate_scores import calculate_score
    conn.row_factory = sqlite3.Row
    prow = conn.execute("SELECT * FROM projects WHERE id='p1'").fetchone()
    sc, _ = calculate_score(conn, prow)
    conn.execute("UPDATE projects SET execution_score=? WHERE id='p1'", (sc,))
    conn.commit()
    conn.close()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_update.db")
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except PermissionError:
            import time; time.sleep(0.1)
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        make_temp_db(self.tmp)
        os.environ["FILDA_DB_PATH"] = self.tmp
        # Stub URL classification so tests never touch the network.
        self._orig = update.classify
        update.classify = lambda url: ("alive", 200)

    def tearDown(self):
        update.classify = self._orig
        os.environ.pop("FILDA_DB_PATH", None)
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except PermissionError:
            pass  # Windows may still hold a lock briefly

    def _count(self, table, where=""):
        conn = sqlite3.connect(f"file:{self.tmp}?mode=ro", uri=True)
        n = conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]
        conn.close()
        return n

    def test_add_event_inserts_and_recomputes(self):
        flags = {"project": "p1", "type": "mou", "date": "2024-06-01",
                 "source_url": "https://example.test/mou"}
        update.cmd_add_event(flags, apply=True)
        self.assertEqual(self._count("events"), 2)
        self.assertEqual(self._count("change_log", "WHERE operation='add-event'"), 1)

    def test_add_event_idempotent(self):
        flags = {"project": "p1", "type": "mou", "date": "2024-06-01",
                 "source_url": "https://example.test/mou"}
        update.cmd_add_event(flags, apply=True)
        update.cmd_add_event(flags, apply=True)  # re-run: no-op
        self.assertEqual(self._count("events"), 2)
        self.assertEqual(self._count("change_log", "WHERE operation='add-event'"), 1)

    def test_add_event_requires_source_url(self):
        flags = {"project": "p1", "type": "mou", "date": "2024-06-01"}
        with self.assertRaises(SystemExit):
            update.cmd_add_event(flags, apply=True)

    def test_set_status_flip_only_without_date(self):
        flags = {"project": "p1", "status": "financed",
                  "source_url": "https://example.test/fin"}
        update.cmd_set_status(flags, apply=True)
        conn = sqlite3.connect(self.tmp)
        st = conn.execute("SELECT status FROM projects WHERE id='p1'").fetchone()[0]
        self.assertEqual(st, "financed")
        # no extra event inserted (no --date)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        conn.close()

    def test_set_status_with_date_inserts_derived_event(self):
        flags = {"project": "p1", "status": "financed", "date": "2024-09-01",
                 "source_url": "https://example.test/fin"}
        update.cmd_set_status(flags, apply=True)
        self.assertEqual(self._count("events"), 2)  # announcement + financing

    def test_add_source_dedup_is_noop(self):
        flags = {"url": "https://example.test/ann", "title": "dup",
                 "publisher": "pub", "date": "2024-01-01"}
        update.cmd_add_source(flags, apply=True)
        self.assertEqual(self._count("sources"), 1)  # already exists -> no-op
        self.assertEqual(self._count("change_log", "WHERE operation='add-source'"), 0)

    def test_relink_score_invariance(self):
        # Add a second source, then relink event 1 to it; score unchanged.
        conn = sqlite3.connect(self.tmp)
        conn.execute("INSERT INTO sources (id, title, url, confidence) "
                     "VALUES (2, 's2', 'https://example.test/alt', 'high')")
        conn.commit()
        before = conn.execute("SELECT execution_score FROM projects WHERE id='p1'").fetchone()[0]
        conn.close()
        flags = {"table": "events", "id": "1",
                 "source_url": "https://example.test/alt"}
        update.cmd_relink(flags, apply=True)
        conn = sqlite3.connect(self.tmp)
        after = conn.execute("SELECT execution_score FROM projects WHERE id='p1'").fetchone()[0]
        sid = conn.execute("SELECT source_id FROM events WHERE id=1").fetchone()[0]
        conn.close()
        self.assertEqual(sid, 2)
        self.assertEqual(before, after)  # source_id is not a formula input

    def test_retype_event_changes_score(self):
        # p1 has an announcement event (3 pts). Re-type to mou (5 pts) -> score moves.
        flags = {"event_id": "1", "to": "mou",
                 "source_url": "https://example.test/ann"}
        update.cmd_retype_event(flags, apply=True)
        conn = sqlite3.connect(self.tmp)
        et = conn.execute("SELECT event_type FROM events WHERE id=1").fetchone()[0]
        self.assertEqual(et, "mou")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE operation='retype-event'").fetchone()[0], 1)
        conn.close()

    # --- set-blocked (Gap 2): label-only flag, no score change ---

    def test_set_blocked_sets_flag_logs_and_keeps_score(self):
        conn = sqlite3.connect(self.tmp)
        before = conn.execute("SELECT execution_score FROM projects WHERE id='p1'").fetchone()[0]
        conn.close()
        flags = {"project": "p1", "to": "1",
                 "source_url": "https://example.test/block"}
        update.cmd_set_blocked(flags, apply=True)
        conn = sqlite3.connect(self.tmp)
        blocked = conn.execute("SELECT is_externally_blocked FROM projects WHERE id='p1'").fetchone()[0]
        after = conn.execute("SELECT execution_score FROM projects WHERE id='p1'").fetchone()[0]
        nlog = conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE operation='set-blocked'").fetchone()[0]
        conn.close()
        self.assertEqual(blocked, 1)
        self.assertEqual(nlog, 1)
        self.assertEqual(before, after)  # label only -- score unchanged

    def test_set_blocked_idempotent(self):
        flags = {"project": "p1", "to": "1",
                 "source_url": "https://example.test/block"}
        update.cmd_set_blocked(flags, apply=True)
        update.cmd_set_blocked(flags, apply=True)  # already 1 -> no-op
        self.assertEqual(self._count("change_log", "WHERE operation='set-blocked'"), 1)

    def test_set_blocked_unblock_with_to_zero(self):
        flags = {"project": "p1", "to": "1",
                 "source_url": "https://example.test/block"}
        update.cmd_set_blocked(flags, apply=True)
        flags["to"] = "0"
        update.cmd_set_blocked(flags, apply=True)  # clear it
        conn = sqlite3.connect(self.tmp)
        blocked = conn.execute("SELECT is_externally_blocked FROM projects WHERE id='p1'").fetchone()[0]
        conn.close()
        self.assertEqual(blocked, 0)

    def test_set_blocked_requires_source_url(self):
        flags = {"project": "p1", "to": "1"}
        with self.assertRaises(SystemExit):
            update.cmd_set_blocked(flags, apply=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
