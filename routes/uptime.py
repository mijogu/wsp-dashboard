"""Uptime Robot proxy route handlers."""
import requests as http_requests

from routes import get_settings, add_log


class UptimeMixin:
    """Mixin for Uptime Robot proxy endpoints."""

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
