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
from urllib.parse import urlparse, parse_qs

import requests as http_requests

from config import (
    save_config, load_config, config_exists,
    export_config, import_config,
)

PORT = 9111
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# In-memory session state
_settings = {}
_passphrase = None
_lock = threading.Lock()


def get_settings():
    with _lock:
        return dict(_settings)


def set_settings(s):
    global _settings
    with _lock:
        _settings = dict(s)


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves static files and handles API proxy routes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ─── Routing ──────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            self._json_response({"unlocked": _passphrase is not None, "hasConfig": config_exists()})
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
        elif path == "/api/settings":
            self._get_settings()
        elif path == "/api/export":
            self._export_config()
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
        else:
            self._json_response({"error": "Not found"}, 404)

    # ─── Auth / Config ────────────────────────────────────────
    def _unlock(self, body):
        global _passphrase
        passphrase = body.get("passphrase", "")
        if not passphrase:
            self._json_response({"error": "Passphrase required"}, 400)
            return

        if config_exists():
            try:
                settings = load_config(passphrase)
                _passphrase = passphrase
                set_settings(settings)
                self._json_response({"ok": True, "settings": settings})
            except Exception:
                self._json_response({"error": "Wrong passphrase"}, 401)
        else:
            # First time — set passphrase, empty config
            _passphrase = passphrase
            set_settings({})
            self._json_response({"ok": True, "settings": {}})

    def _get_settings(self):
        if not _passphrase:
            self._json_response({"error": "Locked"}, 401)
            return
        self._json_response(get_settings())

    def _save_settings(self, body):
        if not _passphrase:
            self._json_response({"error": "Locked"}, 401)
            return
        settings = body.get("settings", {})
        set_settings(settings)
        try:
            save_config(settings, _passphrase)
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _export_config(self):
        if not _passphrase:
            self._json_response({"error": "Locked"}, 401)
            return
        data = export_config(_passphrase)
        self._json_response({"data": data})

    def _import_config(self, body):
        if not _passphrase:
            self._json_response({"error": "Locked"}, 401)
            return
        try:
            settings = import_config(body.get("data", ""), _passphrase)
            set_settings(settings)
            self._json_response({"ok": True, "settings": settings})
        except Exception as e:
            self._json_response({"error": f"Import failed: {e}"}, 400)

    # ─── Uptime Robot Proxy ───────────────────────────────────
    def _proxy_uptime_robot(self):
        s = get_settings()
        api_key = s.get("urApiKey")
        if not api_key:
            self._json_response({"error": "No Uptime Robot API key configured"}, 400)
            return
        try:
            resp = http_requests.post(
                "https://api.uptimerobot.com/v2/getMonitors",
                json={
                    "api_key": api_key,
                    "format": "json",
                    "logs": 1,
                    "log_types": "1-2",
                    "logs_limit": 10,
                    "response_times": 1,
                    "response_times_limit": 1,
                    "custom_uptime_ratios": "1-7-30",
                },
                timeout=30,
            )
            self._json_response(resp.json())
        except Exception as e:
            self._json_response({"error": str(e)}, 502)

    # ─── Cloudflare Proxy ─────────────────────────────────────
    def _proxy_cf_zones(self):
        s = get_settings()
        token = s.get("cfApiToken")
        if not token:
            self._json_response({"error": "No Cloudflare API token configured"}, 400)
            return
        try:
            resp = http_requests.get(
                "https://api.cloudflare.com/client/v4/zones",
                params={"per_page": 50, "status": "active"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            self._json_response(resp.json())
        except Exception as e:
            self._json_response({"error": str(e)}, 502)

    def _proxy_cf_analytics(self, zone_id):
        s = get_settings()
        token = s.get("cfApiToken")
        if not token:
            self._json_response({"error": "No Cloudflare API token configured"}, 400)
            return
        try:
            from datetime import datetime, timedelta, timezone
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            until = datetime.now(timezone.utc).isoformat()
            resp = http_requests.get(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/analytics/dashboard",
                params={"since": since, "until": until},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            self._json_response(resp.json())
        except Exception as e:
            self._json_response({"error": str(e)}, 502)

    # ─── MainWP Proxy ─────────────────────────────────────────
    def _proxy_mainwp_sites(self):
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return
        try:
            resp = http_requests.get(
                f"{base_url}/wp-json/mainwp/v2/sites/basic",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            self._json_response(resp.json())
        except Exception as e:
            self._json_response({"error": str(e)}, 502)

    def _proxy_mainwp_updates(self):
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return
        try:
            resp = http_requests.get(
                f"{base_url}/wp-json/mainwp/v2/updates",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            self._json_response(resp.json())
        except Exception as e:
            self._json_response({"error": str(e)}, 502)

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
    os.chdir(STATIC_DIR)
    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"\n  WP Maintenance Dashboard")
    print(f"  ────────────────────────")
    print(f"  Running at: http://localhost:{PORT}")
    print(f"  Config:     {'config.enc found' if config_exists() else 'no config yet (first run)'}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
