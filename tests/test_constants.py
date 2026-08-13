import os
import sys
import sqlite3
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db"))
from constants import execution_band, EXECUTION_BANDS, band_distribution
from constants import ALLOWED_OPS

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "schema.sql")


class ExecutionBandTests(unittest.TestCase):
    """Tests for the coarse public-facing execution_band label (Gap 3).

    Hybrid derivation: status sets the band, the score refines the upper bands.
    Pure function of (status, execution_score, evidence_complete) -- no DB.
    """

    def test_unscored_is_unconfirmed_regardless_of_status_or_score(self):
        self.assertEqual(execution_band("announced", 0, 0), "UNCONFIRMED")
        self.assertEqual(execution_band("operational", 90, 0), "UNCONFIRMED")
        self.assertEqual(execution_band("completed", 100, 0), "UNCONFIRMED")

    def test_delayed_or_suspended_is_stalled(self):
        self.assertEqual(execution_band("delayed", 3, 1), "STALLED")
        self.assertEqual(execution_band("suspended", 50, 1), "STALLED")

    def test_completed_is_delivered_even_with_low_score(self):
        # status wins: a completed project is DELIVERED regardless of score.
        self.assertEqual(execution_band("completed", 95, 1), "DELIVERED")
        self.assertEqual(execution_band("completed", 50, 1), "DELIVERED")

    def test_score_81_plus_is_delivered_even_if_announced(self):
        self.assertEqual(execution_band("announced", 81, 1), "DELIVERED")
        self.assertEqual(execution_band("operational", 81, 1), "DELIVERED")

    def test_operational_or_mid_score_is_in_progress(self):
        self.assertEqual(execution_band("operational", 78, 1), "IN_PROGRESS")
        self.assertEqual(execution_band("under_construction", 50, 1), "IN_PROGRESS")
        # a 41-80 score promotes an announced project up to IN_PROGRESS
        self.assertEqual(execution_band("announced", 50, 1), "IN_PROGRESS")

    def test_low_score_announced_is_silent(self):
        self.assertEqual(execution_band("announced", 30, 1), "SILENT")   # ETU case
        self.assertEqual(execution_band("announced", 15, 1), "SILENT")
        self.assertEqual(execution_band("unknown", 10, 1), "SILENT")

    def test_every_band_is_reachable(self):
        seen = {execution_band(s, sc, ec) for s, sc, ec in [
            ("announced", 0, 0),       # UNCONFIRMED
            ("delayed", 3, 1),         # STALLED
            ("completed", 95, 1),      # DELIVERED
            ("operational", 78, 1),    # IN_PROGRESS
            ("announced", 30, 1),      # SILENT
        ]}
        self.assertEqual(seen, set(EXECUTION_BANDS))

    def test_band_distribution_counts_and_coverage(self):
        rows = [("announced", 0, 0), ("delayed", 3, 1), ("completed", 95, 1),
                ("operational", 78, 1), ("announced", 30, 1), ("operational", 81, 1)]
        dist = band_distribution(rows)
        self.assertEqual(dist["UNCONFIRMED"], 1)
        self.assertEqual(dist["STALLED"], 1)
        self.assertEqual(dist["DELIVERED"], 2)   # completed-95 + operational-81
        self.assertEqual(dist["IN_PROGRESS"], 1)  # operational-78
        self.assertEqual(dist["SILENT"], 1)       # announced-30
        self.assertEqual(sum(dist.values()), len(rows))
        # every band key present (even if 0)
        self.assertEqual(set(dist), set(EXECUTION_BANDS))


class ChangeLogOperationCheckTests(unittest.TestCase):
    """M10: change_log.operation is enum-CHECKed at the schema level to match
    constants.ALLOWED_OPS. A bogus operation must be rejected at insert/load
    time, and every op db/update.py + load.py/export_csv.py actually writes
    (the full ALLOWED_OPS set) must be accepted — so the schema CHECK and the
    Python vocabulary can never drift out of sync."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        with open(SCHEMA, encoding="utf-8") as f:
            conn.executescript(f.read())
        return conn

    def test_schema_rejects_unknown_operation(self):
        conn = self._conn()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO change_log (operation, target_table) "
                "VALUES ('bogus-op', 'projects')")

    def test_schema_accepts_every_allowed_op(self):
        conn = self._conn()
        for op in ALLOWED_OPS:
            conn.execute(
                "INSERT INTO change_log (operation, target_table) "
                "VALUES (?, 'projects')", (op,))
        conn.commit()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM change_log").fetchone()[0],
            len(ALLOWED_OPS))

    def test_schema_check_literals_match_allowed_ops(self):
        """The CHECK enum literals in schema.sql must be exactly
        constants.ALLOWED_OPS — single-source. Parses the change_log table's
        operation CHECK out of the schema text (SQL can't import Python), so a
        future edit to one without the other fails here instead of silently
        allowing a bogus op at load time or rejecting a real one."""
        import re
        with open(SCHEMA, encoding="utf-8") as f:
            sql = f.read()
        # Grab the change_log CREATE TABLE block, then the operation CHECK.
        m = re.search(r"CREATE TABLE IF NOT EXISTS change_log \(.*?\n\);",
                      sql, re.S)
        self.assertIsNotNone(m, "change_log table not found in schema.sql")
        block = m.group(0)
        # Find the CHECK (...) on the operation column: collect quoted literals.
        check = re.search(r"operation\s+TEXT.*?CHECK\s*\((.*?)\)\s*,",
                          block, re.S | re.I)
        self.assertIsNotNone(check, "operation CHECK not found in change_log")
        literals = re.findall(r"'([^']+)'", check.group(1))
        self.assertEqual(set(literals), set(ALLOWED_OPS),
                         f"schema CHECK ops {set(literals)} != "
                         f"ALLOWED_OPS {set(ALLOWED_OPS)}")


class CentralizedVocabTests(unittest.TestCase):
    """L6: EVENT_TYPES / STATUS_TYPES / CONFIDENCE_LEVELS / CASE_STUDIES are
    single-sourced in constants.py. The scoring dicts in calculate_scores
    (BASE_SCORES / EVENT_POINTS / CONFIDENCE_MULT) must key exactly onto the
    constants vocabs — a weight edit that drops or adds a vocab member without
    updating the other fails here. And the schema.sql CHECK enum literals (which
    SQL can't import) must match the constants vocabs so the load-time
    enforcement layer and the Python vocabulary never drift."""

    def test_status_types_match_base_scores_keys(self):
        from constants import STATUS_TYPES
        import calculate_scores as cs
        self.assertEqual(set(cs.BASE_SCORES), set(STATUS_TYPES),
                         f"BASE_SCORES keys {set(cs.BASE_SCORES)} != "
                         f"STATUS_TYPES {set(STATUS_TYPES)}")

    def test_event_types_match_event_points_keys(self):
        from constants import EVENT_TYPES
        import calculate_scores as cs
        self.assertEqual(set(cs.EVENT_POINTS), set(EVENT_TYPES),
                         f"EVENT_POINTS keys {set(cs.EVENT_POINTS)} != "
                         f"EVENT_TYPES {set(EVENT_TYPES)}")

    def test_confidence_levels_match_confidence_mult_keys(self):
        from constants import CONFIDENCE_LEVELS
        import calculate_scores as cs
        self.assertEqual(set(cs.CONFIDENCE_MULT), set(CONFIDENCE_LEVELS),
                         f"CONFIDENCE_MULT keys {set(cs.CONFIDENCE_MULT)} != "
                         f"CONFIDENCE_LEVELS {set(CONFIDENCE_LEVELS)}")

    def test_case_studies_constant_matches_curated_seven(self):
        from constants import CASE_STUDIES
        self.assertEqual(set(CASE_STUDIES), {
            "huatong-angola-industry-awards", "linha-verde-investor-visas",
            "pt-ao-credit-line-2-5b", "pt-ao-credit-line-3-25b",
            "chicomba-water-dam", "investment-portal-georeferenced",
            "etu-energias-leao-ouro-2025",
        })

    def _schema_check_literals(self, table, column):
        """Parse the quoted literals out of `<column> ... CHECK (...)` in the
        `<table>` CREATE TABLE block of schema.sql."""
        import re
        with open(SCHEMA, encoding="utf-8") as f:
            sql = f.read()
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);",
                      sql, re.S)
        self.assertIsNotNone(m, f"{table} table not found in schema.sql")
        block = m.group(0)
        check = re.search(rf"{column}\s+TEXT.*?CHECK\s*\((.*?)\)\s*,",
                          block, re.S | re.I)
        self.assertIsNotNone(check, f"{column} CHECK not found in {table}")
        return set(re.findall(r"'([^']+)'", check.group(1)))

    def test_schema_event_type_check_matches_constants(self):
        from constants import EVENT_TYPES
        self.assertEqual(self._schema_check_literals("events", "event_type"),
                         set(EVENT_TYPES))

    def test_schema_status_check_matches_constants(self):
        from constants import STATUS_TYPES
        self.assertEqual(self._schema_check_literals("projects", "status"),
                         set(STATUS_TYPES))

    def test_schema_confidence_check_matches_constants(self):
        from constants import CONFIDENCE_LEVELS
        self.assertEqual(self._schema_check_literals("sources", "confidence"),
                         set(CONFIDENCE_LEVELS))


if __name__ == "__main__":
    unittest.main(verbosity=2)