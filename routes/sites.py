"""Site configuration route handlers."""
import json as _json

from routes import add_log
from db import (
    get_cached_sites, get_all_site_configs,
    get_site_config, save_site_config,
    get_registered_sites,
)


class SitesMixin:
    """Mixin for site configuration endpoints."""

    def _get_sites_with_configs(self):
        """Return sites merged with their per-site config.

        Uses the permanent registry if populated; falls back to the live
        MainWP cache so the endpoint works before the first sync.
        """
        registered = get_registered_sites()
        if registered:
            sites_raw = registered
            use_registry = True
        else:
            sites_raw = get_cached_sites()
            use_registry = False

        configs = get_all_site_configs()
        result = []
        for s in sites_raw:
            sid = str(s.get("id", ""))
            cfg = configs.get(sid, {})
            entry = {
                "id": s.get("id"),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "client_name": cfg.get("client_name"),
                "notes": cfg.get("notes"),
                "test_pages": cfg.get("test_pages", "[]"),
                "diff_threshold": cfg.get("diff_threshold", 1.0),
                "config_updated_at": cfg.get("updated_at"),
            }
            if use_registry:
                entry["added_at"] = s.get("added_at")
                entry["last_seen_at"] = s.get("last_seen_at")
                entry["removed_from_mainwp_at"] = s.get("removed_from_mainwp_at")
                entry["is_removed"] = s.get("removed_from_mainwp_at") is not None
            else:
                entry["is_removed"] = False
            result.append(entry)
        self._json_response(result)

    def _get_registered_sites(self):
        """Return the permanent site registry merged with per-site config."""
        registered = get_registered_sites()
        configs = get_all_site_configs()
        result = []
        for s in registered:
            sid = str(s.get("id", ""))
            cfg = configs.get(sid, {})
            result.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "added_at": s.get("added_at"),
                "last_seen_at": s.get("last_seen_at"),
                "removed_from_mainwp_at": s.get("removed_from_mainwp_at"),
                "is_removed": s.get("removed_from_mainwp_at") is not None,
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
