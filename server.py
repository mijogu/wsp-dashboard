#!/usr/bin/env python3
"""
WP Maintenance Dashboard — Local Server
Proxies API calls to MainWP, Uptime Robot, and Cloudflare.
Run: python server.py
Then open: http://localhost:9111
"""

import csv
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import requests as http_requests

from config import (
    save_config, load_config, config_exists,
    export_config, import_config,
    save_session, load_session, clear_session, session_exists,
)
from db import (
    init_db, save_update_records, get_update_history,
    get_history_stats, cache_sites, get_cached_sites, get_cache_age,
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
                sites_list = (data if isinstance(data, list)
                              else data.get("data") or data.get("sites") or [])
                if sites_list:
                    add_log("MainWP", "ok", f"Got {len(sites_list)} sites")
                    try:
                        cache_sites(sites_list)
                        add_log("DB", "ok", f"Cached {len(sites_list)} sites")
                    except Exception as e:
                        add_log("DB", "warn", f"Site cache failed: {e}")
                else:
                    add_log("MainWP", "warn",
                            f"Unexpected 200 structure: keys="
                            f"{list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
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
    def _proxy_mainwp_update_history(self, parsed):
        """
        Fetch historical update data from Pro Reports for all sites.
        Query params:
          ?days=30        — lookback period (default 30)
          ?format=csv     — return CSV instead of JSON
          ?site_id=123    — optional: limit to one site
        """
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return

        qs = parse_qs(parsed.query)
        days = int(qs.get("days", ["30"])[0])
        fmt = qs.get("format", ["json"])[0]
        filter_site = qs.get("site_id", [None])[0]

        auth_headers = {"Authorization": f"Bearer {api_key}"}
        json_headers = {**auth_headers, "Content-Type": "application/json"}

        # Date range
        date_to = int(time.time())
        date_from = date_to - (days * 86400)
        add_log("ProReports", "info",
                f"Fetching update history: last {days} days "
                f"({datetime.fromtimestamp(date_from).strftime('%Y-%m-%d')} → "
                f"{datetime.fromtimestamp(date_to).strftime('%Y-%m-%d')})")

        # Step 1: Get all sites
        try:
            sites_url = f"{base_url}/wp-json/mainwp/v2/sites"
            resp = http_requests.get(sites_url, headers=json_headers, timeout=30)
            sites_data = resp.json()
            sites = sites_data.get("data") or sites_data.get("result") or (
                sites_data if isinstance(sites_data, list) else [])
            add_log("ProReports", "info", f"Got {len(sites)} sites to query")
        except Exception as e:
            add_log("ProReports", "error", f"Failed to get sites: {e}")
            self._json_response({"error": f"Failed to get sites: {e}"}, 502)
            return

        if filter_site:
            sites = [st for st in sites if str(st.get("id")) == str(filter_site)]
            if not sites:
                self._json_response({"error": f"Site {filter_site} not found"}, 404)
                return

        # Step 2: Probe the first site to discover the correct request format
        probe_site = sites[0]
        probe_id = probe_site.get("id")
        probe_name = probe_site.get("name", "Unknown")
        probe_url = probe_site.get("url", "").rstrip("/")
        # Extract domain from URL (strip protocol)
        probe_domain = probe_url.replace("https://", "").replace("http://", "").rstrip("/")
        probe_ep_id = f"{base_url}/wp-json/mainwp/v2/pro-reports/{probe_id}/plugins"
        probe_ep_domain = f"{base_url}/wp-json/mainwp/v2/pro-reports/{probe_domain}/plugins"

        add_log("ProReports", "info",
                f"Probing {probe_name} (id={probe_id}, domain={probe_domain})...")

        winning_method = None

        # Date formats to try
        iso_from = datetime.fromtimestamp(date_from).strftime('%Y-%m-%d')
        iso_to = datetime.fromtimestamp(date_to).strftime('%Y-%m-%d')
        us_from = datetime.fromtimestamp(date_from).strftime('%m/%d/%Y')
        us_to = datetime.fromtimestamp(date_to).strftime('%m/%d/%Y')

        # Build a comprehensive set of probes
        probe_attempts = []

        # For each endpoint variant (by ID and by domain)
        for ep_label, ep in [("id", probe_ep_id), ("domain", probe_ep_domain)]:
            # Param name combos × date format combos
            param_combos = [
                # (label, start_key, end_key, start_val, end_val)
                ("unix date_from/to", "date_from", "date_to", date_from, date_to),
                ("ISO date_from/to", "date_from", "date_to", iso_from, iso_to),
                ("unix start/end", "start", "end", date_from, date_to),
                ("ISO start/end", "start", "end", iso_from, iso_to),
                ("US date_from/to", "date_from", "date_to", us_from, us_to),
                ("camel dateFrom/To", "dateFrom", "dateTo", iso_from, iso_to),
                ("camel startDate/endDate", "startDate", "endDate", iso_from, iso_to),
                ("start_date/end_date", "start_date", "end_date", iso_from, iso_to),
                ("unix start_date/end_date", "start_date", "end_date", date_from, date_to),
            ]
            for plabel, sk, ek, sv, ev in param_combos:
                probe_attempts.append((
                    f"{ep_label} {plabel} action=updated", "get",
                    {"url": f"{ep}?{sk}={sv}&{ek}={ev}&action=updated",
                     "headers": json_headers}))

        # Also try without the action param but with different date param names
        # (in case `action` was accepted silently but the date names are the issue)
        for sk, ek, sv, ev in [
            ("start_date", "end_date", iso_from, iso_to),
            ("startDate", "endDate", iso_from, iso_to),
            ("dateFrom", "dateTo", iso_from, iso_to),
        ]:
            probe_attempts.append((
                f"id {sk}/{ek} NO action", "get",
                {"url": f"{probe_ep_id}?{sk}={sv}&{ek}={ev}",
                 "headers": json_headers}))

        # We'll save the winning URL pattern to reuse for all sites
        winning_url_tpl = None
        uses_domain = False

        for method_name, http_method, kwargs in probe_attempts:
            try:
                kwargs["timeout"] = 30
                if http_method == "get":
                    test_resp = http_requests.get(**kwargs)
                else:
                    test_resp = http_requests.post(**kwargs)
                body = test_resp.text[:500]
                add_log("ProReports", "info",
                        f"  Probe {method_name}: HTTP {test_resp.status_code} → {body}")
                if test_resp.status_code == 200:
                    winning_method = method_name
                    # Figure out if it's id-based or domain-based
                    uses_domain = method_name.startswith("domain ")
                    # Extract the query string from the winning URL
                    ref_ep = probe_ep_domain if uses_domain else probe_ep_id
                    winning_url_tpl = kwargs["url"].replace(ref_ep, "{ep}")
                    add_log("ProReports", "ok",
                            f"  ✓ '{method_name}' works! "
                            f"Uses {'domain' if uses_domain else 'id'}. "
                            f"Template: {winning_url_tpl}")
                    break
            except Exception as e:
                add_log("ProReports", "warn", f"  Probe {method_name}: {e}")

        if not winning_method:
            add_log("ProReports", "error",
                    "All request methods returned errors — see probe responses above.")
            self._json_response({
                "days": days,
                "error": "Pro Reports endpoints rejected all request formats. "
                         "Check the server logs for the raw error responses.",
                "total_records": 0,
                "sites_queried": len(sites),
                "records": [],
            })
            return

        # Step 3: Fetch data from all sites using the winning URL template
        all_records = []
        report_types = ["plugins", "themes", "wordpress"]

        def _fetch_report(site_identifier, rtype):
            """Make a pro-reports request using the discovered format."""
            ep = f"{base_url}/wp-json/mainwp/v2/pro-reports/{site_identifier}/{rtype}"
            url = winning_url_tpl.replace("{ep}", ep)
            return http_requests.get(url, headers=json_headers, timeout=30)

        first_site = True
        for site in sites:
            site_id = site.get("id")
            site_name = site.get("name", "Unknown")
            site_url = site.get("url", "")
            # Use domain or id depending on what worked in probing
            if uses_domain:
                site_identifier = site_url.replace("https://", "").replace("http://", "").rstrip("/")
            else:
                site_identifier = site_id

            for rtype in report_types:
                try:
                    resp = _fetch_report(site_identifier, rtype)
                    if resp.status_code != 200:
                        add_log("ProReports", "warn",
                                f"  {site_name}/{rtype}: HTTP {resp.status_code}")
                        continue

                    data = resp.json()

                    # Pro Reports structure:
                    # {success:1, site:{...}, data:{sections_data:[[{...},{...}]]}}
                    # sections_data is an array of arrays; each inner array
                    # has dicts with bracket-wrapped keys like [plugin.name]
                    sections = None
                    if isinstance(data, dict):
                        inner = data.get("data")
                        if isinstance(inner, dict):
                            sections = inner.get("sections_data")

                    if sections and isinstance(sections, list):
                        # Flatten: sections_data is [[rec, rec], [rec, rec]]
                        for section in sections:
                            if not isinstance(section, list):
                                continue
                            for raw_rec in section:
                                if not isinstance(raw_rec, dict):
                                    continue
                                # Clean bracket-wrapped keys:
                                # "[plugin.name]" → "name"
                                # "[plugin.old.version]" → "old_version"
                                rec = {}
                                for k, v in raw_rec.items():
                                    clean = k.strip("[]")
                                    # Remove the type prefix (plugin./theme./wordpress.)
                                    parts = clean.split(".")
                                    if len(parts) >= 2:
                                        # e.g. plugin.name → name
                                        # plugin.old.version → old_version
                                        # plugin.updated.date → updated_date
                                        clean = "_".join(parts[1:])
                                    rec[clean] = v
                                rec["_site_id"] = site_id
                                rec["_site_name"] = site_name
                                rec["_site_url"] = site_url
                                rec["_update_type"] = rtype
                                all_records.append(rec)

                        if first_site:
                            count = sum(
                                len(s) for s in sections if isinstance(s, list))
                            add_log("ProReports", "ok",
                                    f"  {rtype}: {count} entries")
                            # Log cleaned field names from first record
                            if all_records:
                                sample = {k: v for k, v in all_records[-1].items()
                                          if not k.startswith("_")}
                                add_log("ProReports", "ok",
                                        f"  {rtype} fields: {sorted(sample.keys())}")
                    else:
                        # Fallback for unexpected structures
                        if first_site:
                            add_log("ProReports", "warn",
                                    f"  {rtype}: no sections_data found, "
                                    f"keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

                except Exception as e:
                    add_log("ProReports", "warn",
                            f"  {site_name}/{rtype}: {e}")

            first_site = False
            site_count = sum(1 for r in all_records if r.get("_site_id") == site_id)
            if site_count:
                add_log("ProReports", "ok",
                        f"  {site_name}: {site_count} update records")

        add_log("ProReports", "ok",
                f"Total: {len(all_records)} records across {len(sites)} sites")

        # Step 4: Persist to SQLite
        df = datetime.fromtimestamp(date_from).isoformat()
        dt = datetime.fromtimestamp(date_to).isoformat()
        if all_records:
            try:
                db_stats = save_update_records(
                    all_records, df, dt, days, len(sites))
                add_log("DB", "ok",
                        f"Saved to DB — {db_stats['new']} new, "
                        f"{db_stats['duplicate']} already stored")
            except Exception as e:
                add_log("DB", "warn", f"DB save failed: {e}")

        # Step 5: Return JSON or CSV
        if fmt == "csv":
            # Export from DB so CSV always has the full archive, not just this fetch
            try:
                db_records = get_update_history(days=days)
                output = self._records_to_csv(db_records)
                filename = f"update-history-{days}d.csv"
            except Exception:
                output = self._records_to_csv(all_records)
                filename = f"update-history-{days}d-live.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f"attachment; filename={filename}")
            self.end_headers()
            self.wfile.write(output.encode("utf-8"))
        else:
            self._json_response({
                "days": days,
                "date_from": df,
                "date_to": dt,
                "total_records": len(all_records),
                "sites_queried": len(sites),
                "records": all_records,
            })

    def _serve_cached_history(self, parsed):
        """Return stored update history from SQLite — no MainWP API call needed."""
        qs = parse_qs(parsed.query)
        days = int(qs.get("days", [0])[0])       # 0 = all time
        fmt = qs.get("format", ["json"])[0]
        update_type = qs.get("type", ["all"])[0]
        site_id = qs.get("site_id", [None])[0]

        try:
            records = get_update_history(
                days=days if days else None,
                update_type=update_type if update_type != "all" else None,
                site_id=site_id,
            )
            if fmt == "csv":
                output = self._records_to_csv(records)
                label = f"{days}d" if days else "all"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f"attachment; filename=update-history-{label}.csv")
                self.end_headers()
                self.wfile.write(output.encode("utf-8"))
            else:
                self._json_response({
                    "source": "cache",
                    "days": days,
                    "total_records": len(records),
                    "records": records,
                })
        except Exception as e:
            add_log("DB", "error", f"Cached history query failed: {e}")
            self._json_response({"error": str(e)}, 500)

    def _serve_db_stats(self):
        """Return summary stats about what's in the SQLite DB."""
        try:
            stats = get_history_stats()
            self._json_response(stats)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _records_to_csv(self, records):
        """Convert update history records to CSV string."""
        if not records:
            return ""

        # Preferred column order with friendly headers
        column_spec = [
            ("_site_name", "Site"),
            ("_site_url", "Site URL"),
            ("_update_type", "Type"),
            ("name", "Name"),
            ("old_version", "Old Version"),
            ("current_version", "New Version"),
            ("updated_date", "Date"),
            ("updated_time", "Time"),
            ("updated_utime", "Timestamp"),
            ("updated_author", "Author"),
            ("updated_slug", "Slug"),
        ]

        # Start with known columns, then add any extras we didn't anticipate
        known_fields = {c[0] for c in column_spec}
        skip_fields = {"_site_id"}
        all_keys = set()
        for r in records:
            all_keys.update(r.keys())
        extra = sorted(all_keys - known_fields - skip_fields)

        fieldnames = [c[0] for c in column_spec if c[0] in all_keys] + extra
        header_map = {c[0]: c[1] for c in column_spec}

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow({f: header_map.get(f, f) for f in fieldnames})
        for rec in records:
            writer.writerow(rec)
        return buf.getvalue()

    def _discover_mainwp_routes(self):
        """Discover all registered MainWP REST routes via multiple methods."""
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        # Try multiple discovery URLs in order of specificity
        discovery_urls = [
            (f"{base_url}/wp-json/mainwp/v2", "MainWP v2 namespace"),
            (f"{base_url}/?rest_route=/mainwp/v2", "MainWP v2 via rest_route"),
            (f"{base_url}/wp-json", "Full WP REST index"),
            (f"{base_url}/?rest_route=/", "Full WP REST index via rest_route"),
        ]

        for url, label in discovery_urls:
            add_log("MainWP", "info", f"Route discovery ({label}): {url}")
            try:
                resp = http_requests.get(url, headers=headers, timeout=30)
                content_type = resp.headers.get("Content-Type", "")
                add_log("MainWP", "info",
                        f"  → HTTP {resp.status_code}, Content-Type: {content_type}, "
                        f"{len(resp.text)} bytes")

                # Check if we got HTML instead of JSON
                if "text/html" in content_type or resp.text.strip().startswith("<!"):
                    add_log("MainWP", "warn",
                            f"  → Got HTML, not JSON (security plugin or pretty permalinks off?)")
                    continue

                data = resp.json()

                # If we hit a namespace endpoint, it returns {namespace, routes}
                routes = data.get("routes", {})
                if routes:
                    mwp_routes = {k: v for k, v in routes.items()
                                  if "mainwp" in k.lower()}
                    if not mwp_routes:
                        mwp_routes = routes  # namespace endpoint only has mainwp routes
                    add_log("MainWP", "ok",
                            f"Found {len(mwp_routes)} routes via {label}")

                    # Flag interesting ones
                    report_routes = [r for r in mwp_routes if any(
                        kw in r.lower() for kw in
                        ["report", "log", "history", "update", "client"]
                    )]
                    if report_routes:
                        add_log("MainWP", "ok",
                                f"Potential report routes: {report_routes}")

                    result = {}
                    for route, info in mwp_routes.items():
                        methods = set()
                        for endpoint in info.get("endpoints", []):
                            methods.update(endpoint.get("methods", []))
                        result[route] = sorted(methods)
                    self._json_response({
                        "source": label,
                        "routes": result,
                        "count": len(result),
                    })
                    return

                # Maybe the namespace endpoint returned something else useful
                add_log("MainWP", "info",
                        f"  → Response keys: {list(data.keys()) if isinstance(data, dict) else 'array'}")
                if isinstance(data, dict) and "namespace" in data:
                    add_log("MainWP", "ok",
                            f"  → Namespace: {data['namespace']}")
                    self._json_response(data)
                    return

            except ValueError:
                add_log("MainWP", "warn", f"  → Response not valid JSON")
                continue
            except Exception as e:
                add_log("MainWP", "warn", f"  → Failed: {e}")
                continue

        add_log("MainWP", "error",
                "All discovery methods failed — REST API may be restricted")
        self._json_response({
            "error": "Could not discover routes. REST API index may be blocked. "
                     "Try /api/mainwp/raw/reports and /api/mainwp/raw/client-reports "
                     "to probe directly.",
            "try_manually": [
                "/api/mainwp/raw/reports",
                "/api/mainwp/raw/client-reports",
                "/api/mainwp/raw/pro-reports",
                "/api/mainwp/raw/actions",
                "/api/mainwp/raw/updates",
            ],
        }, 502)

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
            _passphrase = saved_pass
            set_settings(settings)
            add_log("Auth", "ok", "Auto-unlocked from saved session")
        except Exception:
            add_log("Auth", "warn", "Saved session invalid — clearing")
            clear_session()

    os.chdir(STATIC_DIR)

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("127.0.0.1", PORT), DashboardHandler)
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
