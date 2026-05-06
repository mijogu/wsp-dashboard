"""Onboarding checklist route handlers."""
import json as _json

from routes import add_log
import db as _db
import checklist_csv as _csv


class ChecklistsMixin:
    """Mixin for onboarding checklist endpoints."""

    # ── Template ──────────────────────────────────────────────────

    def _checklist_template(self):
        """GET /api/checklists/template — parsed CSV template."""
        try:
            tmpl = _csv.load_checklist_template()
        except FileNotFoundError:
            self._json_response({"error": "checklist CSV not found"}, 503)
            return
        self._json_response(tmpl)

    # ── List / Create ─────────────────────────────────────────────

    def _list_checklists(self, qs=None):
        """GET /api/checklists[?status=active|completed|archived]"""
        status = (qs or {}).get("status", [None])[0] if qs else None
        rows = _db.list_checklists(status)

        # Attach progress % and linked site name to each row
        try:
            tmpl = _csv.load_checklist_template()
            all_steps = tmpl["steps"]
        except Exception:
            all_steps = []

        # Preload all linked site names in one shot
        cached = {str(s["id"]): s.get("name", "") for s in _db.get_cached_sites()}

        result = []
        for row in rows:
            cid = row["id"]
            data = _db.get_checklist_data(cid)
            applicable = _checklist_applicable_steps(row, all_steps)
            total_app = len(applicable)
            completed = sum(
                1 for s in applicable
                if data.get(s["id"], {}).get("value") not in (None, "", "0", "false", False)
            )
            pct = round(completed / total_app * 100) if total_app else 0

            linked_name = None
            if row.get("mainwp_site_id"):
                linked_name = cached.get(str(row["mainwp_site_id"]), f"site #{row['mainwp_site_id']}")

            result.append({
                **row,
                "progress_pct": pct,
                "progress_completed": completed,
                "progress_total": total_app,
                "linked_site_name": linked_name,
            })

        self._json_response(result)

    def _create_checklist(self, body):
        """POST /api/checklists"""
        client_name = (body.get("client_name") or "").strip()
        if not client_name:
            self._json_response({"error": "client_name required"}, 400)
            return
        site_url = (body.get("site_url") or "").strip()
        is_new_client = bool(body.get("is_new_client", True))
        is_redesign = bool(body.get("is_redesign", False))
        is_new_site = bool(body.get("is_new_site", True))
        custom_host = (body.get("custom_host") or "").strip()

        cid = _db.create_checklist(client_name, site_url, is_new_client,
                                   is_redesign, is_new_site, custom_host)
        add_log("Checklists", "ok", f"Checklist created: {client_name} (id={cid})")
        self._json_response({"ok": True, "id": cid})

    # ── Get / Update / Delete ─────────────────────────────────────

    def _get_checklist(self, checklist_id):
        """GET /api/checklists/<id>"""
        row = _db.get_checklist(int(checklist_id))
        if not row:
            self._json_response({"error": "not found"}, 404)
            return
        data = _db.get_checklist_data(int(checklist_id))
        self._json_response({**row, "data": data})

    def _update_checklist(self, checklist_id, body):
        """PATCH /api/checklists/<id>"""
        row = _db.get_checklist(int(checklist_id))
        if not row:
            self._json_response({"error": "not found"}, 404)
            return
        _db.update_checklist(int(checklist_id), **body)
        self._json_response({"ok": True})

    def _delete_checklist(self, checklist_id):
        """DELETE /api/checklists/<id>"""
        row = _db.get_checklist(int(checklist_id))
        if not row:
            self._json_response({"error": "not found"}, 404)
            return
        _db.delete_checklist(int(checklist_id))
        add_log("Checklists", "ok", f"Checklist deleted: id={checklist_id}")
        self._json_response({"ok": True})

    # ── Cell ──────────────────────────────────────────────────────

    def _save_checklist_cell(self, checklist_id, body):
        """POST /api/checklists/<id>/cell"""
        step_id = str(body.get("step_id", "")).strip()
        if not step_id:
            self._json_response({"error": "step_id required"}, 400)
            return
        value = str(body.get("value", ""))
        _db.save_checklist_cell(int(checklist_id), step_id, value)
        self._json_response({"ok": True})

    # ── Link / Unlink ─────────────────────────────────────────────

    def _link_checklist(self, checklist_id, body):
        """POST /api/checklists/<id>/link"""
        try:
            mainwp_site_id = int(body.get("mainwp_site_id"))
        except (TypeError, ValueError):
            self._json_response({"error": "mainwp_site_id required"}, 400)
            return
        _db.link_checklist_to_mainwp(int(checklist_id), mainwp_site_id)
        add_log("Checklists", "ok",
                f"Checklist {checklist_id} linked to site {mainwp_site_id}")
        self._json_response({"ok": True})

    def _unlink_checklist(self, checklist_id):
        """POST /api/checklists/<id>/unlink"""
        _db.unlink_checklist(int(checklist_id))
        self._json_response({"ok": True})

    # ── Available unlinked sites ──────────────────────────────────

    def _checklist_available_sites(self):
        """GET /api/checklists/available-sites — sites not already linked to a checklist."""
        cached = _db.get_cached_sites()
        # Collect all currently linked mainwp_site_ids
        linked_ids = {
            row["mainwp_site_id"]
            for row in _db.list_checklists()
            if row.get("mainwp_site_id")
        }
        sites = [
            {"id": s["id"], "name": s.get("name", ""), "url": s.get("url", "")}
            for s in cached
            if s["id"] not in linked_ids
        ]
        self._json_response(sites)


# ── Helpers ────────────────────────────────────────────────────────────────


def _checklist_applicable_steps(checklist_row: dict, all_steps: list) -> list:
    """Return steps that are 'applicable' (not requiring a condition the checklist doesn't have)."""
    is_new_client = bool(checklist_row.get("is_new_client", True))
    is_redesign = bool(checklist_row.get("is_redesign", False))
    is_new_site = bool(checklist_row.get("is_new_site", True))
    custom_host = (checklist_row.get("custom_host") or "").strip().lower()
    is_custom_host = bool(custom_host and custom_host not in ("", "siteground"))

    result = []
    for step in all_steps:
        if step.get("requires_new_client") and not is_new_client:
            continue
        if step.get("requires_current_client") and is_new_client:
            continue
        if step.get("requires_redesign") and not is_redesign:
            continue
        if step.get("requires_new_site") and not is_new_site:
            continue
        if step.get("requires_custom_host") and not is_custom_host:
            continue
        result.append(step)
    return result
