import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db"))
import export_csv  # noqa: E402
import calculate_scores  # noqa: E402


class AtomicWriteCsvTests(unittest.TestCase):
    """F9: CSVs are the source of truth (the DB is gitignored and rebuilt from
    them), so a kill mid-write must never leave a half-written file. Both
    export_csv.atomic_write_csv and calculate_scores.update_projects_csv write
    to a .tmp sibling then os.replace over the target. These tests pin that
    contract: the target is either the previous complete file or the new one,
    never a truncated half-write, and no .tmp remains on success.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "out.csv")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_atomic_write_csv_replaces_target_and_leaves_no_tmp(self):
        cols = ["id", "name"]
        rows = [{"id": "1", "name": "alpha"}, {"id": "2", "name": "beta"}]
        export_csv.atomic_write_csv(self.path, cols, rows)
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(self._read(), rows)

    def test_atomic_write_csv_overwrites_existing_full_file(self):
        # Pre-existing complete file present.
        export_csv.atomic_write_csv(self.path, ["id"], [{"id": "old"}])
        export_csv.atomic_write_csv(self.path, ["id", "name"],
                                    [{"id": "1", "name": "alpha"}])
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(self._read(), [{"id": "1", "name": "alpha"}])

    def test_atomic_write_csv_empty_rows_still_writes_header(self):
        export_csv.atomic_write_csv(self.path, ["id", "name"], [])
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        with open(self.path, newline="", encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), ["id", "name"])

    def test_calculate_scores_update_projects_csv_is_atomic(self):
        # update_projects_csv reads an existing projects.csv and rewrites the
        # execution_score column via the same .tmp + os.replace pattern. It
        # hardcodes the data/projects.csv path, so redirect that one os.path.join
        # call at a temp file and confirm no .tmp remains + other columns survive.
        cs = calculate_scores
        target = os.path.join(self.tmpdir, "projects.csv")
        fieldnames = ["id", "title", "execution_score"]
        with open(target, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerow({"id": "p1", "title": "Proj", "execution_score": "10"})
        orig_join = cs.os.path.join
        cs.os.path.join = lambda *a: target if a and a[-1] == "projects.csv" \
            else orig_join(*a)
        try:
            changed = cs.update_projects_csv({"p1": (42, {})})
        finally:
            cs.os.path.join = orig_join
        self.assertEqual(changed, 1)
        self.assertFalse(os.path.exists(target + ".tmp"))
        with open(target, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["execution_score"], "42")
        self.assertEqual(row["title"], "Proj")  # other columns preserved


if __name__ == "__main__":
    unittest.main(verbosity=2)