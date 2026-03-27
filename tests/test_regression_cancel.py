"""
Tests for the regression cancel mechanism (regression.py).

Mocks Playwright so tests run without a real browser.

Run with:  python -m pytest tests/test_regression_cancel.py -v
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import regression
import db


class CancelTestBase(unittest.TestCase):
    """Set up a temp screenshot dir and isolated DB for each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._screenshot_dir = os.path.join(self._tmpdir, "screenshots")
        os.makedirs(self._screenshot_dir, exist_ok=True)
        regression._screenshot_dir = self._screenshot_dir
        regression._active_run = None
        regression._cancel_requested = False

        # Isolated DB
        self._db_path = os.path.join(self._tmpdir, "test.db")
        self._db_patcher = patch.object(db, "DB_PATH", self._db_path)
        self._db_patcher.start()
        db._local.conn = None
        db.init_db()

        # Capture log messages
        self.logs = []

    def tearDown(self):
        regression._active_run = None
        regression._cancel_requested = False
        conn = getattr(db._local, "conn", None)
        if conn:
            conn.close()
            db._local.conn = None
        self._db_patcher.stop()

    def _add_log(self, source, level, message):
        self.logs.append((source, level, message))

    def _make_sites(self, n):
        return [{"id": i, "name": f"Site {i}", "url": f"https://site{i}.example.com"}
                for i in range(1, n + 1)]


class TestCancelFlag(CancelTestBase):

    def test_request_cancel_sets_flag(self):
        self.assertFalse(regression._cancel_requested)
        regression.request_cancel()
        self.assertTrue(regression._cancel_requested)

    def test_flag_resets_at_run_start(self):
        """run_checks resets _cancel_requested = False at the start."""
        regression._cancel_requested = True

        # We'll mock Playwright to immediately raise so the run is short
        mock_playwright = MagicMock()
        mock_pw_cm = MagicMock()
        mock_playwright.__enter__ = MagicMock(return_value=mock_pw_cm)
        mock_playwright.__exit__ = MagicMock(return_value=False)
        mock_pw_cm.chromium.launch.side_effect = Exception("mock crash")

        run_id = db.create_regression_run()
        with patch("regression.sync_playwright", return_value=mock_playwright, create=True):
            regression.run_checks(
                self._make_sites(1), self._add_log,
                db.save_regression_result, db.finish_regression_run,
                run_id,
            )
        # Flag should have been reset to False at the start of run_checks
        self.assertFalse(regression._cancel_requested)


class TestCancelDuringRun(CancelTestBase):

    def test_cancel_stops_after_current_site(self):
        """Cancel between sites: sites checked before cancel are saved, rest skipped."""
        sites = self._make_sites(5)
        run_id = db.create_regression_run()
        results_saved = []

        def fake_save(rid, result):
            results_saved.append(result)
            db.save_regression_result(rid, result)

        # Mock Playwright browser/context/page
        mock_page = MagicMock()
        mock_page.goto.return_value = MagicMock(status=200)
        mock_page.evaluate.return_value = None
        mock_page.wait_for_load_state.return_value = None
        mock_page.screenshot.return_value = None

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        mock_playwright_cm = MagicMock()
        mock_playwright_cm.__enter__ = MagicMock(return_value=mock_pw)
        mock_playwright_cm.__exit__ = MagicMock(return_value=False)

        # Request cancel after the 2nd site finishes
        original_check = regression.check_single_site
        check_count = [0]

        def counting_check(page, url, timeout_ms=30000):
            check_count[0] += 1
            # After 2 sites complete, request cancel
            if check_count[0] == 2:
                regression.request_cancel()
            return {
                "http_status": 200,
                "load_time_ms": 500,
                "js_errors": [],
                "broken_resources": [],
                "screenshot_path": f"site{check_count[0]}.png",
                "error": None,
            }

        with patch("regression.sync_playwright", return_value=mock_playwright_cm, create=True), \
             patch("regression.check_single_site", side_effect=counting_check):
            regression.run_checks(
                sites, self._add_log,
                fake_save, db.finish_regression_run,
                run_id,
            )

        # Only 2 sites should have been checked (cancel fires before site 3)
        self.assertEqual(len(results_saved), 2)

        # Run should be marked as cancelled in DB
        runs = db.get_regression_runs()
        self.assertEqual(runs[0]["status"], "cancelled")

        # _active_run should be cleared
        self.assertIsNone(regression.get_active_run())

        # browser.close() should have been called
        mock_browser.close.assert_called()

    def test_cancel_before_any_site(self):
        """Cancel immediately — no sites should be checked."""
        sites = self._make_sites(3)
        run_id = db.create_regression_run()
        results_saved = []

        # Pre-set cancel before run starts (the run resets it, but we set it
        # in the check_single_site mock)
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_playwright_cm = MagicMock()
        mock_playwright_cm.__enter__ = MagicMock(return_value=mock_pw)
        mock_playwright_cm.__exit__ = MagicMock(return_value=False)

        # Set cancel flag right after _cancel_requested is reset but before loop
        original_run = regression.run_checks

        def cancel_immediately_after_reset(*args, **kwargs):
            # We need to set the flag after the reset happens.
            # Since run_checks resets to False at the top, we use a thread
            pass

        # Simpler approach: patch the site loop entry to set cancel
        with patch("regression.sync_playwright", return_value=mock_playwright_cm, create=True):
            # Start in a thread and set cancel very quickly
            t = threading.Thread(
                target=regression.run_checks,
                args=(sites, self._add_log,
                      lambda rid, res: results_saved.append(res),
                      db.finish_regression_run, run_id),
            )

            # Mock check_single_site to be slow enough to catch cancel
            def slow_check(page, url, timeout_ms=30000):
                time.sleep(0.5)
                return {
                    "http_status": 200, "load_time_ms": 100,
                    "js_errors": [], "broken_resources": [],
                    "screenshot_path": None, "error": None,
                }

            with patch("regression.check_single_site", side_effect=slow_check):
                t.start()
                # Wait briefly for the run to start, then cancel
                time.sleep(0.1)
                regression.request_cancel()
                t.join(timeout=10)

        # At most 1 site checked (the one in progress when cancel was set)
        self.assertLessEqual(len(results_saved), 1)

    def test_active_run_cleared_after_cancel(self):
        """After cancel, get_active_run() returns None."""
        sites = self._make_sites(2)
        run_id = db.create_regression_run()

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_playwright_cm = MagicMock()
        mock_playwright_cm.__enter__ = MagicMock(return_value=mock_pw)
        mock_playwright_cm.__exit__ = MagicMock(return_value=False)

        def cancel_on_first(page, url, timeout_ms=30000):
            regression.request_cancel()
            return {
                "http_status": 200, "load_time_ms": 100,
                "js_errors": [], "broken_resources": [],
                "screenshot_path": None, "error": None,
            }

        with patch("regression.sync_playwright", return_value=mock_playwright_cm, create=True), \
             patch("regression.check_single_site", side_effect=cancel_on_first):
            regression.run_checks(
                sites, self._add_log,
                db.save_regression_result, db.finish_regression_run,
                run_id,
            )

        self.assertIsNone(regression.get_active_run())

    def test_cancel_logs_warning(self):
        """Cancellation should emit a warning log."""
        sites = self._make_sites(2)
        run_id = db.create_regression_run()

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_playwright_cm = MagicMock()
        mock_playwright_cm.__enter__ = MagicMock(return_value=mock_pw)
        mock_playwright_cm.__exit__ = MagicMock(return_value=False)

        check_count = [0]

        def check_then_cancel(page, url, timeout_ms=30000):
            check_count[0] += 1
            if check_count[0] == 1:
                regression.request_cancel()
            return {
                "http_status": 200, "load_time_ms": 100,
                "js_errors": [], "broken_resources": [],
                "screenshot_path": None, "error": None,
            }

        with patch("regression.sync_playwright", return_value=mock_playwright_cm, create=True), \
             patch("regression.check_single_site", side_effect=check_then_cancel):
            regression.run_checks(
                sites, self._add_log,
                db.save_regression_result, db.finish_regression_run,
                run_id,
            )

        warn_logs = [msg for src, lvl, msg in self.logs if lvl == "warn"]
        self.assertTrue(any("cancelled" in m.lower() for m in warn_logs),
                        f"Expected cancel warning in logs, got: {warn_logs}")


if __name__ == "__main__":
    unittest.main()
