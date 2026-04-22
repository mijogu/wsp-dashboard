"""Heartbeat scan route handlers."""
import threading

from routes import add_log
from db import (
    get_registered_sites, get_cached_sites,
    create_heartbeat_run, finish_heartbeat_run,
    save_heartbeat_result, get_heartbeat_runs,
    get_heartbeat_results, get_latest_heartbeat_for_site,
    get_heartbeat_history_for_site, get_onboarding_cell,
)
from heartbeat import (
    get_active_run as hb_get_active_run,
    is_running as hb_is_running,
    request_cancel as hb_request_cancel,
    run_heartbeat,
)


class HeartbeatMixin:
    """Mixin for heartbeat scan endpoints."""

    def _heartbeat_status(self):
        self._json_response({
            "active_run": hb_get_active_run(),
            "running": hb_is_running(),
        })

    def _heartbeat_runs(self):
        self._json_response(get_heartbeat_runs())

    def _heartbeat_results(self, run_id_str):
        try:
            run_id = int(run_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id"}, 400)
            return
        runs = get_heartbeat_runs()
        meta = next((r for r in runs if r["id"] == run_id), {})
        meta["results"] = get_heartbeat_results(run_id)
        self._json_response(meta)

    def _heartbeat_site_latest(self, site_id_str):
        try:
            site_id = int(site_id_str)
        except ValueError:
            self._json_response({"error": "Invalid site_id"}, 400)
            return
        result = get_latest_heartbeat_for_site(site_id)
        self._json_response(result or {})

    def _heartbeat_site_history(self, site_id_str):
        try:
            site_id = int(site_id_str)
        except ValueError:
            self._json_response({"error": "Invalid site_id"}, 400)
            return
        self._json_response(get_heartbeat_history_for_site(site_id))

    def _cancel_heartbeat_run(self):
        if not hb_is_running():
            self._json_response({"error": "No heartbeat scan is running"}, 409)
            return
        hb_request_cancel()
        add_log("Heartbeat", "warn", "Heartbeat scan cancellation requested")
        self._json_response({"ok": True})

    def _start_heartbeat_run(self, body=None):
        if hb_is_running():
            self._json_response({
                "error": "A heartbeat scan is already running",
                "active_run": hb_get_active_run(),
            }, 409)
            return

        if body is None:
            body = self._read_body()
        site_ids_filter = body.get("site_ids")

        sites_raw = get_registered_sites() or get_cached_sites()
        if not sites_raw:
            self._json_response({"error": "No sites available"}, 400)
            return

        if site_ids_filter:
            allowed = {str(x) for x in site_ids_filter}
            sites_raw = [s for s in sites_raw
                         if str(s.get("id")) in allowed]
            if not sites_raw:
                self._json_response({"error": "None of the selected sites found"}, 400)
                return

        run_id = create_heartbeat_run()

        def _get_staging_url(site_id):
            return get_onboarding_cell(site_id, "staging_url")

        t = threading.Thread(
            target=run_heartbeat,
            args=(sites_raw, run_id, add_log, save_heartbeat_result,
                  finish_heartbeat_run),
            kwargs={"get_staging_url_fn": _get_staging_url},
            daemon=True,
        )
        t.start()

        add_log("Heartbeat", "info",
                f"Heartbeat run #{run_id} started ({len(sites_raw)} site(s))")
        self._json_response({"ok": True, "run_id": run_id,
                             "total_sites": len(sites_raw)})
