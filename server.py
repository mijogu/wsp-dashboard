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
    save_session, load_session, clear_session, session_exists,
)

PORT = 9111
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# In-memory session state
_settings = {}
_passphrase = None
_lock = threading.Lock()
_logs = []       # recent API call logs
_logs_lock = threading.Lock()
MAX_LOGS = 200


def add_log(source, level, message, detail=None):
    """Add a log entry. level: 'info', 'ok', 'warn', 'error'"""
    import time
    entry = {
        "ts": time.time(),
        "time": time.strftime("%H:%M:%S"),
        "source": source,
        "level": level,
        "message": message,
    }
    if detail:
        entry["detail"] = str(detail)[:500]
    with _logs_lock:
        _logs.append(entry)
        if len(_logs) > MAX_LOGS:
            _logs[:] = _logs[-MAX_LOGS:]
    # Also print to server console
    prefix = {"ok": "+", "info": "~", "warn": "!", "error": "X"}.get(level, "?")
    sys.stderr.write(f"  [{prefix}] {source}: {message}\n")
    if detail:
        sys.stderr.write(f"      {str(detail)[:200]}\n")


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
            self._json_response({
                "unlocked": _passphrase is not None,
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
        elif path.startswith("/api/mainwp/raw/"):
            # Discovery/debug: proxy any MainWP endpoint
            # e.g. /api/mainwp/raw/sites or /api/mainwp/raw/sites/69
            mwp_path = path.replace("/api/mainwp/raw/", "")
            self._proxy_mainwp_raw(mwp_path)
        elif path == "/api/settings":
            self._get_settings()
        elif path == "/api/export":
            self._export_config()
        elif path == "/api/logs":
            with _logs_lock:
                self._json_response(list(_logs))
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
        remember = body.get("remember", False)

        if not passphrase:
            self._json_response({"error": "Passphrase required"}, 400)
            return

        if config_exists():
            try:
                settings = load_config(passphrase)
                _passphrase = passphrase
                set_settings(settings)
                if remember:
                    save_session(passphrase)
                    add_log("Auth", "ok", "Session saved — will auto-unlock on next restart")
                self._json_response({"ok": True, "settings": settings})
            except Exception:
                self._json_response({"error": "Wrong passphrase"}, 401)
        else:
            # First time — set passphrase, empty config
            _passphrase = passphrase
            set_settings({})
            if remember:
                save_session(passphrase)
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
            add_log("Uptime Robot", "warn", "No API key configured")
            self._json_response({"error": "No Uptime Robot API key configured"}, 400)
            return
        url = "https://api.uptimerobot.com/v2/getMonitors"
        add_log("Uptime Robot", "info", f"Requesting {url}")
        try:
            resp = http_requests.post(
                url,
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
            data = resp.json()
            if data.get("stat") == "ok":
                count = len(data.get("monitors", []))
                add_log("Uptime Robot", "ok", f"Got {count} monitors (HTTP {resp.status_code})")
            else:
                add_log("Uptime Robot", "error", f"API error: {data.get('error', {}).get('message', 'unknown')}", data.get("error"))
            self._json_response(data)
        except Exception as e:
            add_log("Uptime Robot", "error", f"Request failed: {e}")
            self._json_response({"error": str(e)}, 502)

    # ─── Cloudflare Proxy ─────────────────────────────────────
    def _proxy_cf_zones(self):
        s = get_settings()
        token = s.get("cfApiToken")
        if not token:
            add_log("Cloudflare", "warn", "No API token configured")
            self._json_response({"error": "No Cloudflare API token configured"}, 400)
            return
        url = "https://api.cloudflare.com/client/v4/zones"
        add_log("Cloudflare", "info", f"Requesting {url}")
        try:
            resp = http_requests.get(
                url,
                params={"per_page": 50, "status": "active"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            data = resp.json()
            if data.get("success"):
                count = len(data.get("result", []))
                add_log("Cloudflare", "ok", f"Got {count} zones (HTTP {resp.status_code})")
            else:
                errors = data.get("errors", [])
                msg = errors[0].get("message", "unknown") if errors else "unknown"
                add_log("Cloudflare", "error", f"API error: {msg}", errors)
            self._json_response(data)
        except Exception as e:
            add_log("Cloudflare", "error", f"Request failed: {e}")
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
            missing = []
            if not base_url: missing.append("URL")
            if not api_key: missing.append("API key")
            add_log("MainWP", "warn", f"Not configured — missing: {', '.join(missing)}")
            self._json_response({"error": "MainWP not configured"}, 400)
            return
        url = f"{base_url}/wp-json/mainwp/v2/sites"
        add_log("MainWP", "info", f"Requesting {url}")
        add_log("MainWP", "info", f"Auth: Bearer token ({len(api_key)} chars, starts with '{api_key[:6]}...')")
        try:
            resp = http_requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            add_log("MainWP", "info", f"HTTP {resp.status_code} — Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
            # Log first 300 chars of raw response for debugging
            raw = resp.text
            add_log("MainWP", "info", f"Raw response preview: {raw[:300]}")
            try:
                data = resp.json()
            except Exception:
                add_log("MainWP", "error", f"Response is not valid JSON (HTTP {resp.status_code})", raw[:500])
                self._json_response({"error": f"MainWP returned non-JSON (HTTP {resp.status_code})"}, 502)
                return
            if resp.status_code == 200:
                if isinstance(data, list):
                    add_log("MainWP", "ok", f"Got {len(data)} sites")
                elif isinstance(data, dict) and (data.get("data") or data.get("sites")):
                    items = data.get("data") or data.get("sites") or []
                    add_log("MainWP", "ok", f"Got {len(items)} sites (wrapped)")
                else:
                    add_log("MainWP", "warn", f"Unexpected 200 response structure: keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
            elif resp.status_code == 401:
                add_log("MainWP", "error", "401 Unauthorized — API key may be invalid or expired")
            elif resp.status_code == 403:
                add_log("MainWP", "error", "403 Forbidden — API key may lack read permissions")
            else:
                add_log("MainWP", "error", f"HTTP {resp.status_code}", data)
            self._json_response(data)
        except http_requests.exceptions.ConnectionError as e:
            add_log("MainWP", "error", f"Connection failed — is {base_url} reachable?", str(e))
            self._json_response({"error": f"Cannot connect to {base_url}: {e}"}, 502)
        except http_requests.exceptions.Timeout:
            add_log("MainWP", "error", f"Request timed out after 30s")
            self._json_response({"error": "MainWP request timed out"}, 502)
        except Exception as e:
            add_log("MainWP", "error", f"Request failed: {e}")
            self._json_response({"error": str(e)}, 502)

    def _proxy_mainwp_updates(self):
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return
        url = f"{base_url}/wp-json/mainwp/v2/updates"
        add_log("MainWP", "info", f"Requesting {url}")
        try:
            resp = http_requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            try:
                data = resp.json()
            except Exception:
                add_log("MainWP", "error", f"Updates response is not valid JSON (HTTP {resp.status_code})")
                self._json_response({"error": f"MainWP updates returned non-JSON (HTTP {resp.status_code})"}, 502)
                return
            if resp.status_code == 200:
                add_log("MainWP", "ok", f"Got updates data (keys: {list(data.keys()) if isinstance(data, dict) else 'array'})")
            else:
                add_log("MainWP", "error", f"Updates HTTP {resp.status_code}", data)
            self._json_response(data)
        except Exception as e:
            add_log("MainWP", "error", f"Updates request failed: {e}")
            self._json_response({"error": str(e)}, 502)

    # ─── MainWP Raw/Discovery Proxy ─────────────────────────
    def _proxy_mainwp_raw(self, mwp_path):
        """Proxy any MainWP v2 endpoint for discovery/debugging."""
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return
        url = f"{base_url}/wp-json/mainwp/v2/{mwp_path}"
        add_log("MainWP", "info", f"Raw request: {url}")
        try:
            resp = http_requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            add_log("MainWP", "info", f"Raw response: HTTP {resp.status_code}, {len(resp.text)} bytes")
            # Log field names for discovery
            try:
                data = resp.json()
                if isinstance(data, dict):
                    add_log("MainWP", "info", f"Response keys: {list(data.keys())}")
                    # If it has a data array, log first item's keys
                    items = data.get("data") or data.get("result") or []
                    if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                        add_log("MainWP", "ok", f"First item keys: {sorted(items[0].keys())}")
                elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                    add_log("MainWP", "ok", f"First item keys: {sorted(data[0].keys())}")
                self._json_response(data)
            except Exception:
                add_log("MainWP", "warn", f"Raw response (not JSON): {resp.text[:300]}")
                self._json_response({"raw": resp.text[:2000]})
        except Exception as e:
            add_log("MainWP", "error", f"Raw request failed: {e}")
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
    global _passphrase

    # Try auto-unlock from saved session
    saved_pass = load_session()
    if saved_pass and config_exists():
        try:
            settings = load_config(saved_pass)
            _passphrase = saved_pass
            set_settings(settings)
            add_log("Auth", "ok", "Auto-unlocked from saved session")
        except Exception:
            add_log("Auth", "warn", "Saved session invalid — clearing")
            clear_session()

    os.chdir(STATIC_DIR)
    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    unlocked = "YES (auto-unlocked)" if _passphrase else "no (unlock in browser)"
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
