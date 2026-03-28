"""Site configuration route handlers."""
import json as _json

from routes import add_log
from db import (
    get_cached_sites, get_all_site_configs,
    get_site_config, save_site_config,
)


class SitesMixin:
    """Mixin for site configuration endpoints."""

    def _get_sites_with_configs(self):
        """Return all cached sites merged with their per-site config."""
        sites = get_cached_sites()
        configs = get_all_site_configs()
        result = []
        for s in sites:
            sid = str(s.get("id", ""))
            cfg = configs.get(sid, {})
            result.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "client_name": cfg.get("client_name"),
                "notes": cfg.get("notes"),
                "test_pages": cfg.get("test_pages", "[]"),
                "diff_threshold": cfg.get("diff_threshold", 1.0),
                "config_updated_at": cfg.get("updated_at"),
            })
        self._json_response(result)

    def _get_site_config(self, site_id):
        """Return config for a single site."""
        self._json_response(get_site_config(site_id))

    def _save_site_config(self, site_id, body):
        """Save config for a single site."""
        client_name = body.get("client_name", "")
        notes = body.get("notes", "")
        test_pages = body.get("test_pages", [])
        # Normalise: ensure it's a JSON string
        if isinstance(test_pages, list):
            test_pages_str = _json.dumps(test_pages)
        else:
            test_pages_str = str(test_pages)
        # diff_threshold: accept float or numeric string; default 1.0
        try:
            diff_threshold = float(body.get("diff_threshold", 1.0))
        except (TypeError, ValueError):
            diff_threshold = 1.0
        save_site_config(site_id, client_name=client_name,
                         notes=notes, test_pages=test_pages_str,
                         diff_threshold=diff_threshold)
        add_log("Sites", "ok", f"Config saved for site {site_id}")
        self._json_response({"ok": True})
