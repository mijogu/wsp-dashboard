"""
Tests for per-site configuration (db.py) and page-routing in regression.py.

Run with:  python -m unittest tests.test_site_config -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import regression


class SiteConfigBase(unittest.TestCase):
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


# ─── DB: site_config CRUD ─────────────────────────────────────────────────────

class TestSiteConfigCRUD(SiteConfigBase):

    def test_get_nonexistent_returns_defaults(self):
        cfg = db.get_site_config(99)
        self.assertEqual(cfg["site_id"], "99")
        self.assertIsNone(cfg["client_name"])
        self.assertIsNone(cfg["notes"])
        self.assertEqual(cfg["test_pages"], "[]")

    def test_save_and_retrieve(self):
        db.save_site_config(42, client_name="Acme Corp",
                            notes="SiteGround hosted",
                            test_pages='["https://acme.com", "https://acme.com/about"]')
        cfg = db.get_site_config(42)
        self.assertEqual(cfg["client_name"], "Acme Corp")
        self.assertEqual(cfg["notes"], "SiteGround hosted")
        pages = json.loads(cfg["test_pages"])
        self.assertEqual(pages, ["https://acme.com", "https://acme.com/about"])

    def test_save_updates_existing(self):
        db.save_site_config(1, client_name="Old Name")
        db.save_site_config(1, client_name="New Name", notes="updated")
        cfg = db.get_site_config(1)
        self.assertEqual(cfg["client_name"], "New Name")
        self.assertEqual(cfg["notes"], "updated")

    def test_empty_strings_stored_as_null(self):
        db.save_site_config(5, client_name="", notes="")
        cfg = db.get_site_config(5)
        self.assertIsNone(cfg["client_name"])
        self.assertIsNone(cfg["notes"])

    def test_get_all_returns_all_configs(self):
        db.save_site_config(1, client_name="Client A")
        db.save_site_config(2, client_name="Client B")
        db.save_site_config(3, client_name="Client C")
        configs = db.get_all_site_configs()
        self.assertEqual(len(configs), 3)
        self.assertIn("1", configs)
        self.assertIn("2", configs)
        self.assertIn("3", configs)
        self.assertEqual(configs["1"]["client_name"], "Client A")

    def test_get_all_empty(self):
        self.assertEqual(db.get_all_site_configs(), {})

    def test_site_id_stored_as_string(self):
        db.save_site_config(7, client_name="Numeric ID")
        cfg = db.get_site_config("7")   # string lookup
        self.assertEqual(cfg["client_name"], "Numeric ID")
        cfg2 = db.get_site_config(7)    # int lookup
        self.assertEqual(cfg2["client_name"], "Numeric ID")


# ─── DB: page_url column in regression_results ───────────────────────────────

class TestPageUrlColumn(SiteConfigBase):

    def test_page_url_saved_and_retrieved(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, {
            "site_id": 1, "site_name": "Test", "site_url": "https://test.com",
            "page_url": "https://test.com/about",
            "http_status": 200, "load_time_ms": 500,
            "js_errors": "[]", "broken_resources": "[]",
            "screenshot_path": None, "has_issues": 0, "error": None,
        })
        results = db.get_regression_results(run_id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["page_url"], "https://test.com/about")

    def test_page_url_null_when_omitted(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, {
            "site_id": 1, "site_name": "Test", "site_url": "https://test.com",
            "http_status": 200, "load_time_ms": 500,
            "js_errors": "[]", "broken_resources": "[]",
            "screenshot_path": None, "has_issues": 0, "error": None,
        })
        results = db.get_regression_results(run_id)
        self.assertIsNone(results[0]["page_url"])


# ─── regression.py: _get_pages_for_site ──────────────────────────────────────

class TestGetPagesForSite(unittest.TestCase):

    def test_no_config_returns_site_url(self):
        pages = regression._get_pages_for_site("https://example.com", 1, None)
        self.assertEqual(pages, ["https://example.com"])

    def test_empty_configs_returns_site_url(self):
        pages = regression._get_pages_for_site("https://example.com", 1, {})
        self.assertEqual(pages, ["https://example.com"])

    def test_config_with_pages_returns_them(self):
        configs = {"1": {"test_pages": '["https://example.com", "https://example.com/about"]'}}
        pages = regression._get_pages_for_site("https://example.com", 1, configs)
        self.assertEqual(pages, ["https://example.com", "https://example.com/about"])

    def test_config_with_empty_pages_falls_back(self):
        configs = {"1": {"test_pages": "[]"}}
        pages = regression._get_pages_for_site("https://example.com", 1, configs)
        self.assertEqual(pages, ["https://example.com"])

    def test_config_with_list_object(self):
        # Already-parsed list (not a JSON string)
        configs = {"5": {"test_pages": ["https://site.com", "https://site.com/contact"]}}
        pages = regression._get_pages_for_site("https://site.com", 5, configs)
        self.assertEqual(pages, ["https://site.com", "https://site.com/contact"])

    def test_config_with_malformed_json_falls_back(self):
        configs = {"1": {"test_pages": "not valid json{"}}
        pages = regression._get_pages_for_site("https://example.com", 1, configs)
        self.assertEqual(pages, ["https://example.com"])

    def test_site_id_string_lookup(self):
        # site_id passed as int, config keyed by string
        configs = {"3": {"test_pages": '["https://test.com/a", "https://test.com/b"]'}}
        pages = regression._get_pages_for_site("https://test.com", 3, configs)
        self.assertEqual(len(pages), 2)

    def test_blank_urls_filtered_out(self):
        configs = {"1": {"test_pages": '["https://example.com", "", "  "]'}}
        pages = regression._get_pages_for_site("https://example.com", 1, configs)
        self.assertEqual(pages, ["https://example.com"])


# ─── regression.py: run_checks total_pages count ─────────────────────────────

class TestRunChecksPageCount(SiteConfigBase):

    def setUp(self):
        super().setUp()
        regression._screenshot_dir = self._tmpdir
        regression._active_run = None
        regression._cancel_requested = False

    def tearDown(self):
        super().tearDown()
        regression._active_run = None
        regression._cancel_requested = False

    def test_multiple_pages_per_site_all_saved(self):
        """Each configured page produces its own regression_result row."""
        sites = [{"id": 1, "name": "Site A", "url": "https://site-a.com"}]
        site_configs = {
            "1": {"test_pages": '["https://site-a.com", "https://site-a.com/about", "https://site-a.com/contact"]'}
        }
        run_id = db.create_regression_run()
        results_saved = []

        mock_page = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_page.goto.return_value = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(status=200)
        mock_page.evaluate.return_value = None
        mock_page.wait_for_load_state.return_value = None
        mock_page.screenshot.return_value = None

        mock_context = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_cm.__enter__ = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_pw)
        mock_cm.__exit__ = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=False)

        def fake_check(page, url, timeout_ms=30000):
            results_saved.append(url)
            return {"http_status": 200, "load_time_ms": 300,
                    "js_errors": [], "broken_resources": [],
                    "screenshot_path": None, "error": None}

        with patch("regression.sync_playwright", return_value=mock_cm, create=True), \
             patch("regression.check_single_site", side_effect=fake_check):
            regression.run_checks(
                sites, lambda *a: None,
                db.save_regression_result, db.finish_regression_run,
                run_id, site_configs=site_configs,
            )

        # All 3 pages should have been checked and saved
        self.assertEqual(len(results_saved), 3)
        self.assertIn("https://site-a.com", results_saved)
        self.assertIn("https://site-a.com/about", results_saved)
        self.assertIn("https://site-a.com/contact", results_saved)

        db_results = db.get_regression_results(run_id)
        self.assertEqual(len(db_results), 3)
        page_urls = {r["page_url"] for r in db_results}
        self.assertEqual(page_urls, {"https://site-a.com",
                                     "https://site-a.com/about",
                                     "https://site-a.com/contact"})


if __name__ == "__main__":
    unittest.main()
