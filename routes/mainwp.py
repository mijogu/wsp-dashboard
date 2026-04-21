"""MainWP proxy route handlers."""
import csv
import io
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import requests as http_requests

from routes import get_settings, add_log
from db import (
    save_update_records, get_update_history,
    get_history_stats, get_last_fetch_date,
    cache_sites, upsert_sites,
)


class MainWPMixin:
    """Mixin for MainWP proxy endpoints."""

    @staticmethod
    def _fetch_all_mainwp_sites(base_url, json_headers, per_page=100):
        """
        Paginate through /mainwp/v2/sites until all sites are collected.
        Returns (sites_list, error_message_or_None).
        MainWP defaults to 20 per page; we request 100 and keep paging.
        """
        all_sites = []
        page = 1
        while True:
            url = f"{base_url}/wp-json/mainwp/v2/sites?per_page={per_page}&page={page}"
            resp = http_requests.get(url, headers=json_headers, timeout=30)
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code} fetching sites (page {page})"
            try:
                data = resp.json()
            except Exception as e:
                return None, f"Non-JSON sites response (page {page}): {e}"

            # Unwrap common envelope shapes
            batch = (data if isinstance(data, list)
                     else data.get("data") or data.get("sites") or [])
            if not batch:
                break   # empty page → done
            all_sites.extend(batch)
            if len(batch) < per_page:
                break   # last page (partial)
            page += 1
        return all_sites, None

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

        json_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        add_log("MainWP", "info",
                f"Requesting all sites from {base_url} (paginated, per_page=100)")
        try:
            sites_list, err = self._fetch_all_mainwp_sites(base_url, json_headers)
            if err:
                add_log("MainWP", "error", f"Sites fetch failed: {err}")
                self._json_response({"error": err}, 502)
                return

            add_log("MainWP", "ok", f"Got {len(sites_list)} sites total")
            try:
                cache_sites(sites_list)
                add_log("DB", "ok", f"Cached {len(sites_list)} sites")
            except Exception as e:
                add_log("DB", "warn", f"Site cache failed: {e}")
            try:
                upsert_sites(sites_list)
                add_log("DB", "ok", f"Registry updated: {len(sites_list)} sites")
            except Exception as e:
                add_log("DB", "warn", f"Site registry update failed: {e}")

            self._json_response(sites_list)
        except http_requests.exceptions.ConnectionError as e:
            add_log("MainWP", "error", f"Connection failed — is {base_url} reachable?", str(e))
            self._json_response({"error": f"Cannot connect to {base_url}: {e}"}, 502)
        except http_requests.exceptions.Timeout:
            add_log("MainWP", "error", "Request timed out after 30s")
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

    def _proxy_mainwp_update_history(self, parsed):
        """
        Fetch historical update data from Pro Reports for all sites.
        Query params:
          ?start_date=YYYY-MM-DD  — explicit start (overrides incremental default)
          ?end_date=YYYY-MM-DD    — explicit end (default: today)
          ?format=csv             — return CSV instead of JSON
          ?site_id=123            — optional: limit to one site

        Default behaviour (no start_date given):
          - start_date = last fetch's date_to from DB (incremental sync)
          - if no prior fetch, falls back to 30 days ago
        """
        s = get_settings()
        base_url = s.get("mwpUrl", "").rstrip("/")
        api_key = s.get("mwpApiKey")
        if not base_url or not api_key:
            self._json_response({"error": "MainWP not configured"}, 400)
            return

        auth_headers = {"Authorization": f"Bearer {api_key}"}
        json_headers = {**auth_headers, "Content-Type": "application/json"}

        qs = parse_qs(parsed.query)
        fmt = qs.get("format", ["json"])[0]
        filter_site = qs.get("site_id", [None])[0]

        today = datetime.now().strftime("%Y-%m-%d")

        # Determine date range
        end_date_str = qs.get("end_date", [today])[0]
        if "start_date" in qs:
            start_date_str = qs["start_date"][0]
            sync_mode = "backfill"
        else:
            last_fetch = get_last_fetch_date()
            if last_fetch:
                start_date_str = last_fetch
                sync_mode = "incremental"
            else:
                # First ever fetch — go back 30 days
                start_date_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                sync_mode = "initial"

        # Convert ISO dates to unix timestamps (for probe URL building)
        try:
            dt_from = datetime.strptime(start_date_str, "%Y-%m-%d")
            dt_to   = datetime.strptime(end_date_str,   "%Y-%m-%d")
        except ValueError as e:
            self._json_response({"error": f"Invalid date format: {e}"}, 400)
            return

        date_from = int(dt_from.timestamp())
        date_to   = int(dt_to.timestamp())
        days = max(1, (dt_to - dt_from).days)

        add_log("ProReports", "info",
                f"Fetching update history ({sync_mode}): "
                f"{start_date_str} → {end_date_str} ({days} days)")

        # Step 1: Get all sites (paginated)
        try:
            sites, err = self._fetch_all_mainwp_sites(base_url, json_headers)
            if err or sites is None:
                raise RuntimeError(err or "Unknown error fetching sites")
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

        # Step 2: Fetch data from all sites
        # Confirmed format: ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&action=updated
        # with site ID (not domain) in the endpoint path.
        all_records = []
        report_types = ["plugins", "themes", "wordpress"]

        def _fetch_report(site_id, rtype):
            """Fetch one report type for one site using the known-good API format."""
            url = (f"{base_url}/wp-json/mainwp/v2/pro-reports/{site_id}/{rtype}"
                   f"?start_date={start_date_str}&end_date={end_date_str}&action=updated")
            return http_requests.get(url, headers=json_headers, timeout=30)

        for site in sites:
            site_id = site.get("id")
            site_name = site.get("name", "Unknown")
            site_url = site.get("url", "")

            for rtype in report_types:
                try:
                    resp = _fetch_report(site_id, rtype)
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

                    else:
                        # Fallback for unexpected structures — warn for every site
                        add_log("ProReports", "warn",
                                f"  {site_name}/{rtype}: no sections_data found, "
                                f"keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

                except Exception as e:
                    add_log("ProReports", "warn",
                            f"  {site_name}/{rtype}: {e}")

            site_count = sum(1 for r in all_records if r.get("_site_id") == site_id)
            add_log("ProReports", "ok",
                    f"  {site_name}: {site_count} update records")

        add_log("ProReports", "ok",
                f"Total: {len(all_records)} records across {len(sites)} sites")

        # Step 4: Persist to SQLite
        if all_records:
            try:
                db_stats = save_update_records(
                    all_records, start_date_str, end_date_str, days, len(sites))
                add_log("DB", "ok",
                        f"Saved to DB — {db_stats['new']} new, "
                        f"{db_stats['duplicate']} already stored")
            except Exception as e:
                add_log("DB", "warn", f"DB save failed: {e}")

        # Step 5: Return JSON or CSV
        if fmt == "csv":
            # Export from DB so CSV always has the full archive, not just this fetch
            try:
                db_records = get_update_history()
                output = self._records_to_csv(db_records)
                filename = f"update-history-{start_date_str}-to-{end_date_str}.csv"
            except Exception:
                output = self._records_to_csv(all_records)
                filename = f"update-history-{start_date_str}-to-{end_date_str}-live.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f"attachment; filename={filename}")
            self.end_headers()
            self.wfile.write(output.encode("utf-8"))
        else:
            self._json_response({
                "sync_mode": sync_mode,
                "date_from": start_date_str,
                "date_to": end_date_str,
                "days": days,
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
            # Include last fetch date for incremental-sync UI
            stats["last_fetch_date"] = get_last_fetch_date()
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
