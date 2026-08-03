import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db"))
from constants import execution_band, EXECUTION_BANDS, band_distribution


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


if __name__ == "__main__":
    unittest.main(verbosity=2)