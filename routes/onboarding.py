"""Onboarding table route handlers."""
import json as _json
import re

from routes import add_log
from db import (
    get_onboarding_fields, create_onboarding_field,
    update_onboarding_field, delete_onboarding_field,
    get_onboarding_data, save_onboarding_cell,
    get_registered_sites, get_cached_sites, get_all_site_configs,
)


def _slugify(text: str) -> str:
    """Convert display name to a safe slug for use as a field ID."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "field"


class OnboardingMixin:
    """Mixin for onboarding table endpoints."""

    def _get_onboarding_fields(self):
        self._json_response(get_onboarding_fields())

    def _create_onboarding_field(self, body):
        name = (body.get("name") or "").strip()
        if not name:
            self._json_response({"error": "name required"}, 400)
            return
        group_name = (body.get("group_name") or "General").strip()
        field_type = body.get("field_type", "text")
        if field_type not in ("text", "url", "bool", "select"):
            field_type = "text"
        options = body.get("options", [])
        if isinstance(options, list):
            options = _json.dumps(options)
        default_value = str(body.get("default_value", ""))

        # Use provided id or generate from name; ensure uniqueness by appending counter
        fid = _slugify(body.get("id") or name)
        existing_ids = {f["id"] for f in get_onboarding_fields()}
        base = fid
        counter = 2
        while fid in existing_ids:
            fid = f"{base}_{counter}"
            counter += 1

        create_onboarding_field(fid, name, group_name, field_type,
                                options, default_value)
        add_log("Onboarding", "ok", f"Field created: {fid}")
        self._json_response({"ok": True, "id": fid})

    def _update_onboarding_field(self, fid, body):
        allowed = {"name", "group_name", "field_type", "options",
                   "hidden", "position", "default_value"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if "options" in updates and isinstance(updates["options"], list):
            updates["options"] = _json.dumps(updates["options"])
        if "hidden" in updates:
            updates["hidden"] = int(bool(updates["hidden"]))
        update_onboarding_field(fid, **updates)
        self._json_response({"ok": True})

    def _delete_onboarding_field(self, fid):
        delete_onboarding_field(fid)
        add_log("Onboarding", "ok", f"Field deleted: {fid}")
        self._json_response({"ok": True})

    def _get_onboarding_data(self):
        fields = get_onboarding_fields()
        cell_data = get_onboarding_data()

        # Build site list (same logic as _get_sites_with_configs)
        registered = get_registered_sites()
        sites_raw = registered if registered else get_cached_sites()
        configs = get_all_site_configs()

        sites = []
        for s in sites_raw:
            sid = str(s.get("id", ""))
            cfg = configs.get(sid, {})
            if cfg.get("hidden_from_onboarding"):
                continue
            sites.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "is_removed": s.get("removed_from_mainwp_at") is not None,
            })

        self._json_response({
            "fields": fields,
            "sites": sites,
            "data": cell_data,
        })

    def _save_onboarding_cell(self, body):
        try:
            site_id = int(body.get("site_id"))
        except (TypeError, ValueError):
            self._json_response({"error": "site_id required"}, 400)
            return
        field_id = str(body.get("field_id", "")).strip()
        if not field_id:
            self._json_response({"error": "field_id required"}, 400)
            return
        value = str(body.get("value", ""))
        save_onboarding_cell(site_id, field_id, value)
        self._json_response({"ok": True})
