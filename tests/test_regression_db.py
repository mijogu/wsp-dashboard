"""
Tests for regression-related DB operations (cancel + delete features).

Run with:  python -m pytest tests/test_regression_db.py -v
       or: python -m unittest tests.test_regression_db -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db


class RegressionDBBase(unittest.TestCase):
    """Each test gets an isolated temp DB."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        self._patcher = patch.object(db, "DB_PATH", self._db_path)
        self._patcher.start()
        db._local.conn = None
        db.init_db()

    def tearDown(self):
        conn = getattr(db._local, "conn", None)
        if conn:
            conn.close()
            db._local.conn = None
        self._patcher.stop()

    def _make_result(self, run_id, site_id=1, site_name="Test Site",
                     site_url="https://test.example.com", http_status=200,
                     load_time_ms=1500, screenshot_path="test_20260319.png",
                     has_issues=0, error=None):
        return {
            "site_id": site_id,
            "site_name": site_name,
            "site_url": site_url,
            "http_status": http_status,
            "load_time_ms": load_time_ms,
            "js_errors": "[]",
            "broken_resources": "[]",
            "screenshot_path": screenshot_path,
            "has_issues": has_issues,
            "error": error,
        }


# ─── Create / Finish Run ──────────────────────────────────────────────────────

class TestCreateRun(RegressionDBBase):

    def test_create_run_returns_integer_id(self):
        run_id = db.create_regression_run()
        self.assertIsInstance(run_id, int)
        self.assertGreater(run_id, 0)

    def test_create_multiple_runs_increments(self):
        id1 = db.create_regression_run()
        id2 = db.create_regression_run()
        self.assertEqual(id2, id1 + 1)

    def test_new_run_has_running_status(self):
        run_id = db.create_regression_run()
        runs = db.get_regression_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "running")
        self.assertIsNone(runs[0]["finished_at"])


class TestFinishRun(RegressionDBBase):

    def test_finish_run_sets_status_and_counts(self):
        run_id = db.create_regression_run()
        db.finish_regression_run(run_id, total=5, issues=2, status="completed")
        runs = db.get_regression_runs()
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["total_sites"], 5)
        self.assertEqual(runs[0]["issues_found"], 2)
        self.assertIsNotNone(runs[0]["finished_at"])

    def test_finish_run_cancelled_status(self):
        run_id = db.create_regression_run()
        db.finish_regression_run(run_id, total=3, issues=0, status="cancelled")
        runs = db.get_regression_runs()
        self.assertEqual(runs[0]["status"], "cancelled")


# ─── Save and Retrieve Results ─────────────────────────────────────────────────

class TestSaveResults(RegressionDBBase):

    def test_save_and_retrieve_results(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, self._make_result(run_id, site_name="Site A"))
        db.save_regression_result(run_id, self._make_result(run_id, site_id=2,
                                                             site_name="Site B",
                                                             screenshot_path="b.png"))
        results = db.get_regression_results(run_id)
        self.assertEqual(len(results), 2)
        names = {r["site_name"] for r in results}
        self.assertEqual(names, {"Site A", "Site B"})

    def test_results_belong_to_correct_run(self):
        r1 = db.create_regression_run()
        r2 = db.create_regression_run()
        db.save_regression_result(r1, self._make_result(r1, site_name="Run1Site"))
        db.save_regression_result(r2, self._make_result(r2, site_name="Run2Site"))
        self.assertEqual(len(db.get_regression_results(r1)), 1)
        self.assertEqual(db.get_regression_results(r1)[0]["site_name"], "Run1Site")
        self.assertEqual(db.get_regression_results(r2)[0]["site_name"], "Run2Site")


# ─── Delete Regression Run ────────────────────────────────────────────────────

class TestDeleteRun(RegressionDBBase):

    def test_delete_removes_run_row(self):
        run_id = db.create_regression_run()
        db.finish_regression_run(run_id, 1, 0, "completed")
        db.delete_regression_run(run_id)
        runs = db.get_regression_runs()
        self.assertEqual(len(runs), 0)

    def test_delete_removes_associated_results(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, self._make_result(run_id))
        db.save_regression_result(run_id, self._make_result(run_id, site_id=2,
                                                             screenshot_path="s2.png"))
        db.delete_regression_run(run_id)
        results = db.get_regression_results(run_id)
        self.assertEqual(len(results), 0)

    def test_delete_returns_screenshot_paths(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, self._make_result(
            run_id, screenshot_path="alpha.png"))
        db.save_regression_result(run_id, self._make_result(
            run_id, site_id=2, screenshot_path="beta.png"))
        db.save_regression_result(run_id, self._make_result(
            run_id, site_id=3, screenshot_path=None))  # no screenshot
        paths = db.delete_regression_run(run_id)
        self.assertEqual(sorted(paths), ["alpha.png", "beta.png"])

    def test_delete_returns_empty_list_when_no_screenshots(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, self._make_result(
            run_id, screenshot_path=None))
        paths = db.delete_regression_run(run_id)
        self.assertEqual(paths, [])

    def test_delete_nonexistent_run_is_safe(self):
        # Should not raise
        paths = db.delete_regression_run(99999)
        self.assertEqual(paths, [])

    def test_delete_only_affects_target_run(self):
        r1 = db.create_regression_run()
        r2 = db.create_regression_run()
        db.save_regression_result(r1, self._make_result(r1, site_name="Keep"))
        db.save_regression_result(r2, self._make_result(r2, site_name="Delete"))
        db.finish_regression_run(r1, 1, 0, "completed")
        db.finish_regression_run(r2, 1, 0, "completed")

        db.delete_regression_run(r2)

        # r1 still intact
        runs = db.get_regression_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], r1)
        results = db.get_regression_results(r1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["site_name"], "Keep")


# ─── Get Runs Ordering ────────────────────────────────────────────────────────

class TestGetRuns(RegressionDBBase):

    def test_runs_ordered_newest_first(self):
        r1 = db.create_regression_run()
        r2 = db.create_regression_run()
        r3 = db.create_regression_run()
        runs = db.get_regression_runs()
        self.assertEqual([r["id"] for r in runs], [r3, r2, r1])

    def test_get_runs_limit(self):
        for _ in range(5):
            db.create_regression_run()
        runs = db.get_regression_runs(limit=2)
        self.assertEqual(len(runs), 2)


# ─── Latest Run ───────────────────────────────────────────────────────────────

class TestLatestRun(RegressionDBBase):

    def test_latest_returns_none_when_empty(self):
        self.assertIsNone(db.get_latest_regression_run())

    def test_latest_returns_most_recent_completed(self):
        r1 = db.create_regression_run()
        db.save_regression_result(r1, self._make_result(r1, site_name="A"))
        db.finish_regression_run(r1, 1, 0, "completed")

        r2 = db.create_regression_run()
        db.save_regression_result(r2, self._make_result(r2, site_name="B"))
        db.finish_regression_run(r2, 1, 0, "completed")

        latest = db.get_latest_regression_run()
        self.assertEqual(latest["id"], r2)
        self.assertEqual(len(latest["results"]), 1)
        self.assertEqual(latest["results"][0]["site_name"], "B")


if __name__ == "__main__":
    unittest.main()
