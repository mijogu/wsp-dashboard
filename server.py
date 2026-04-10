#!/usr/bin/env python3
"""
WP Maintenance Dashboard — Local Server
Proxies API calls to MainWP, Uptime Robot, and Cloudflare.
Run: python server.py
Then open: http://localhost:9111
"""

import json
import os
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import routes
from routes import add_log, get_settings, set_settings
from routes.auth import AuthMixin
from routes.uptime import UptimeMixin
from routes.cloudflare import CloudflareMixin
from routes.mainwp import MainWPMixin
from routes.sites import SitesMixin
from routes.regression import RegressionMixin

from config import (
    load_config, config_exists,
    load_session, clear_session, session_exists,
)
from db import init_db, get_history_stats
from regression import init_regression

PORT = 9111
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class DashboardHandler(
    AuthMixin, UptimeMixin, CloudflareMixin,
    MainWPMixin, SitesMixin, RegressionMixin,
    SimpleHTTPRequestHandler,
):
    """Serves static files and handles API proxy routes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ─── Routing ──────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            self._json_response({
                "unlocked": routes.get_passphrase() is not None,
                "hasConfig": config_exists(),
                "hasSession": session_exists(),
            })
        elif path == "/api/uptime-robot":
            self._proxy_uptime_robot()
        elif path == "/api/cloudflare/zones":
            self._proxy_cf_zones()
        elif path.startswith("/api/cloudflare/analytics/"):
            zone_id = path.split("/")[-1]
            self._proxy_cf_analytics(zone_id)
        elif path == "/api/mainwp/sites":
            self._proxy_mainwp_sites()
        elif path == "/api/mainwp/updates":
            self._proxy_mainwp_updates()
        elif path == "/api/mainwp/routes":
            self._discover_mainwp_routes()
        elif path == "/api/mainwp/update-history":
            self._proxy_mainwp_update_history(parsed)
        elif path == "/api/mainwp/update-history/cached":
            self._serve_cached_history(parsed)
        elif path == "/api/db/stats":
            self._serve_db_stats()
        elif path.startswith("/api/mainwp/raw/"):
            # Discovery/debug: proxy any MainWP endpoint
            # e.g. /api/mainwp/raw/sites or /api/mainwp/raw/sites/69
            mwp_path = path.replace("/api/mainwp/raw/", "")
            self._proxy_mainwp_raw(mwp_path)
        elif path == "/api/settings":
            self._get_settings()
        elif path == "/api/export":
            self._export_config()
        elif path == "/api/regression/site-status":
            self._regression_site_status()
        elif path == "/api/regression/status":
            self._regression_status()
        elif path == "/api/regression/runs":
            self._regression_runs()
        elif path == "/api/regression/latest":
            self._regression_latest()
        elif path.startswith("/api/regression/results/"):
            run_id = path.split("/")[-1]
            self._regression_results(run_id)
        elif path.startswith("/api/regression/screenshot/"):
            filename = path.split("/")[-1]
            self._serve_screenshot(filename)
        elif path.startswith("/api/regression/site/") and path.endswith("/history"):
            site_id = path.split("/")[-2]
            self._regression_site_history(site_id)
        elif path == "/api/sites":
            self._get_sites_with_configs()
        elif path.startswith("/api/sites/config/"):
            site_id = path.split("/")[-1]
            self._get_site_config(site_id)
        elif path == "/api/logs":
            with routes._logs_lock:
                self._json_response(list(routes._logs))
        else:
            # Serve static files; default to index.html
            if path == "/":
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/unlock":
            self._unlock(body)
        elif path == "/api/settings":
            self._save_settings(body)
        elif path == "/api/import":
            self._import_config(body)
        elif path == "/api/regression/run":
            self._start_regression_run(body)
        elif path == "/api/regression/cancel":
            self._cancel_regression_run()
        elif path.startswith("/api/sites/config/"):
            site_id = path.split("/")[-1]
            self._save_site_config(site_id, body)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/regression/run/"):
            run_id = path.split("/")[-1]
            self._delete_regression_run(run_id)
        else:
            self._json_response({"error": "Not found"}, 404)

    # ─── Helpers ──────────────────────────────────────────────
    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quieter logging — only show errors and API calls
        msg = format % args
        if "/api/" in msg or "404" in msg or "500" in msg:
            sys.stderr.write(f"[dashboard] {msg}\n")


def main():
    # Initialize regression screenshot directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    init_regression(base_dir)

    # Warn if Pillow is missing — visual diff will silently skip without it
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        add_log("Regression", "warn",
                "Pillow not installed — visual diff disabled. "
                "Fix: pip install Pillow")

    # Initialize SQLite DB (creates file + tables if not present)
    try:
        init_db()
        stats = get_history_stats()
        if stats["total_records"]:
            add_log("DB", "ok",
                    f"Database ready — {stats['total_records']} update records "
                    f"({stats['unique_sites']} sites, "
                    f"oldest: {(stats['oldest_record'] or '')[:10]})")
        else:
            add_log("DB", "info", "Database ready — no records yet")
    except Exception as e:
        add_log("DB", "warn", f"DB init failed: {e}")

    # Try auto-unlock from saved session
    saved_pass = load_session()
    if saved_pass and config_exists():
        try:
            settings = load_config(saved_pass)
            routes.set_passphrase(saved_pass)
            set_settings(settings)
            add_log("Auth", "ok", "Auto-unlocked from saved session")
        except Exception:
            add_log("Auth", "warn", "Saved session invalid — clearing")
            clear_session()

    os.chdir(STATIC_DIR)

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    unlocked = "YES (auto-unlocked)" if routes.get_passphrase() else "no (unlock in browser)"
    print(f"\n  WP Maintenance Dashboard")
    print(f"  ────────────────────────")
    print(f"  Running at: http://localhost:{PORT}")
    print(f"  Config:     {'config.enc found' if config_exists() else 'no config yet (first run)'}")
    print(f"  Session:    {unlocked}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
