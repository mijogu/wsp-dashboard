"""Cloudflare proxy route handlers."""
import requests as http_requests

from routes import get_settings, add_log


class CloudflareMixin:
    """Mixin for Cloudflare proxy endpoints."""

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
