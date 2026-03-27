"""
Tests for regression API endpoints in server.py (cancel + delete).

Uses a real test HTTP server to exercise the full request/response cycle.

Run with:  python -m pytest tests/test_server_regression.py -v
"""

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import regression
import server as srv

# Use urllib since requests may not be installed in test env
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def _get_free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class ServerTestBase(unittest.TestCase):
    """Spin up a real test server with isolated DB per test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        self._screenshot_dir = os.path.join(self._tmpdir, "screenshots")
        os.makedirs(self._screenshot_dir, exist_ok=True)

        # Fresh DB for each test
        self._db_patcher = patch.object(db, "DB_PATH", self._db_path)
        self._db_patcher.start()
        db._local.conn = None
        db.init_db()

        regression._screenshot_dir = self._screenshot_dir
        regression._active_run = None
        regression._cancel_requested = False

        self._port = _get_free_port()
        self._httpd = HTTPServer(("127.0.0.1", self._port), srv.DashboardHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever)
        self._thread.daemon = True
        self._thread.start()

    def tearDown(self):
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        regression._active_run = None
        regression._cancel_requested = False
        conn = getattr(db._local, "conn", None)
        if conn:
            conn.close()
            db._local.conn = None
        self._db_patcher.stop()
        # Clean up screenshot files
        for f in os.listdir(self._screenshot_dir):
            os.remove(os.path.join(self._screenshot_dir, f))

    def _url(self, path):
        return f"http://127.0.0.1:{self._port}{path}"

    def _get(self, path):
        return json.loads(urlopen(self._url(path)).read())

    def _post(self, path, data=None):
        body = json.dumps(data or {}).encode()
        req = Request(self._url(path), data=body,
                      headers={"Content-Type": "application/json"})
        return json.loads(urlopen(req).read())

    def _delete(self, path):
        req = Request(self._url(path), method="DELETE")
        return json.loads(urlopen(req).read())

    def _delete_expect_error(self, path, expected_status):
        req = Request(self._url(path), method="DELETE")
        try:
            urlopen(req)
            self.fail(f"Expected HTTP {expected_status}")
        except HTTPError as e:
            self.assertEqual(e.code, expected_status)
            return json.loads(e.read())

    def _post_expect_error(self, path, data=None, expected_status=409):
        body = json.dumps(data or {}).encode()
        req = Request(self._url(path), data=body,
                      headers={"Content-Type": "application/json"})
        try:
            urlopen(req)
            self.fail(f"Expected HTTP {expected_status}")
        except HTTPError as e:
            self.assertEqual(e.code, expected_status)
            return json.loads(e.read())


# ─── Cancel Endpoint ──────────────────────────────────────────────────────────

class TestCancelEndpoint(ServerTestBase):

    def test_cancel_no_active_run_returns_409(self):
        resp = self._post_expect_error("/api/regression/cancel", expected_status=409)
        self.assertIn("error", resp)

    def test_cancel_with_active_run_returns_ok(self):
        # Simulate an active run
        regression._active_run = {
            "run_id": 1, "status": "running", "total": 5,
            "checked": 2, "issues_found": 0, "current_site": "Test",
        }
        resp = self._post("/api/regression/cancel")
        self.assertTrue(resp.get("ok"))
        self.assertTrue(regression._cancel_requested)


# ─── Delete Endpoint ──────────────────────────────────────────────────────────

class TestDeleteEndpoint(ServerTestBase):

    def test_delete_completed_run(self):
        run_id = db.create_regression_run()
        db.save_regression_result(run_id, {
            "site_id": 1, "site_name": "Test", "site_url": "https://test.com",
            "http_status": 200, "load_time_ms": 1000,
            "js_errors": "[]", "broken_resources": "[]",
            "screenshot_path": "test_shot.png", "has_issues": 0, "error": None,
        })
        db.finish_regression_run(run_id, 1, 0, "completed")

        # Create a fake screenshot file
        with open(os.path.join(self._screenshot_dir, "test_shot.png"), "wb") as f:
            f.write(b"fakepng")

        resp = self._delete(f"/api/regression/run/{run_id}")
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp["deleted_screenshots"], 1)

        # Verify DB is clean
        self.assertEqual(len(db.get_regression_runs()), 0)
        self.assertEqual(len(db.get_regression_results(run_id)), 0)

        # Verify screenshot file is gone
        self.assertFalse(os.path.exists(
            os.path.join(self._screenshot_dir, "test_shot.png")))

    def test_delete_invalid_run_id_returns_400(self):
        resp = self._delete_expect_error("/api/regression/run/abc", 400)
        self.assertIn("error", resp)

    def test_delete_active_run_returns_409(self):
        run_id = db.create_regression_run()
        regression._active_run = {
            "run_id": run_id, "status": "running", "total": 5,
            "checked": 1, "issues_found": 0, "current_site": "Test",
        }
        resp = self._delete_expect_error(f"/api/regression/run/{run_id}", 409)
        self.assertIn("cancel", resp["error"].lower())

    def test_delete_nonexistent_run_still_returns_ok(self):
        resp = self._delete("/api/regression/run/99999")
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp["deleted_screenshots"], 0)

    def test_delete_preserves_other_runs(self):
        r1 = db.create_regression_run()
        r2 = db.create_regression_run()
        db.save_regression_result(r1, {
            "site_id": 1, "site_name": "Keep", "site_url": "https://keep.com",
            "http_status": 200, "load_time_ms": 100,
            "js_errors": "[]", "broken_resources": "[]",
            "screenshot_path": None, "has_issues": 0, "error": None,
        })
        db.save_regression_result(r2, {
            "site_id": 2, "site_name": "Delete", "site_url": "https://del.com",
            "http_status": 200, "load_time_ms": 100,
            "js_errors": "[]", "broken_resources": "[]",
            "screenshot_path": None, "has_issues": 0, "error": None,
        })
        db.finish_regression_run(r1, 1, 0, "completed")
        db.finish_regression_run(r2, 1, 0, "completed")

        self._delete(f"/api/regression/run/{r2}")

        runs = db.get_regression_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], r1)
        self.assertEqual(len(db.get_regression_results(r1)), 1)

    def test_delete_run_with_multiple_screenshots(self):
        run_id = db.create_regression_run()
        for i in range(3):
            fname = f"shot_{i}.png"
            db.save_regression_result(run_id, {
                "site_id": i, "site_name": f"Site {i}",
                "site_url": f"https://site{i}.com",
                "http_status": 200, "load_time_ms": 100,
                "js_errors": "[]", "broken_resources": "[]",
                "screenshot_path": fname, "has_issues": 0, "error": None,
            })
            with open(os.path.join(self._screenshot_dir, fname), "wb") as f:
                f.write(b"png")
        db.finish_regression_run(run_id, 3, 0, "completed")

        resp = self._delete(f"/api/regression/run/{run_id}")
        self.assertEqual(resp["deleted_screenshots"], 3)

        # All screenshots cleaned up
        remaining = os.listdir(self._screenshot_dir)
        self.assertEqual(len(remaining), 0)


# ─── Status Endpoint ──────────────────────────────────────────────────────────

class TestStatusEndpoint(ServerTestBase):

    def test_status_no_active_run(self):
        resp = self._get("/api/regression/status")
        self.assertIsNone(resp.get("active_run"))

    def test_status_with_active_run(self):
        regression._active_run = {
            "run_id": 42, "status": "running", "total": 10,
            "checked": 3, "issues_found": 1, "current_site": "Test",
        }
        resp = self._get("/api/regression/status")
        self.assertIsNotNone(resp["active_run"])
        self.assertEqual(resp["active_run"]["run_id"], 42)


if __name__ == "__main__":
    unittest.main()
