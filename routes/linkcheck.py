"""Link checker route handlers."""
import threading

from routes import get_settings, add_log
from db import (
    get_cached_sites,
    get_registered_sites,
    get_all_site_configs,
    create_link_check_run,
    save_link_check_result,
    save_link_check_site_run,
    finish_link_check_run,
    get_link_check_runs,
    get_link_check_results,
    get_link_check_results_for_run,
    get_latest_link_check_run,
    update_link_check_run_totals,
    get_link_check_site_status,
    get_link_check_site_history,
    get_link_check_run_site_results,
)
from link_checker import (
    run_link_check,
    get_active_check,
    request_cancel as request_link_check_cancel,
)


class LinkCheckMixin:
    """Mixin for link checker endpoints."""

    def _get_link_check_site_status(self):
        """Return per-site summary from the latest completed run."""
        self._json_response(get_link_check_site_status())

    def _get_link_check_site_history(self, site_id_str):
        """Return per-run summary for one site, newest first."""
        try:
            site_id = int(site_id_str)
        except ValueError:
            self._json_response({"error": "Invalid site_id"}, 400)
            return
        self._json_response(get_link_check_site_history(site_id))

    def _get_link_check_run_site_results(self, run_id_str, site_id_str):
        """Return broken links for one site within one run."""
        try:
            run_id  = int(run_id_str)
            site_id = int(site_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id or site_id"}, 400)
            return
        self._json_response(get_link_check_run_site_results(run_id, site_id))

    def _get_link_check_status(self):
        """Return active run state, or last run info if nothing is running."""
        active = get_active_check()
        if active:
            self._json_response({"active_run": active})
            return
        runs = get_link_check_runs()
        last = runs[0] if runs else None
        self._json_response({"active_run": None, "last_run": last})

    def _get_link_check_runs(self):
        """Return all past link check runs, newest first."""
        self._json_response(get_link_check_runs())

    def _get_latest_link_check(self):
        """Return most recent completed run with per-site summaries."""
        run = get_latest_link_check_run()
        if not run:
            self._json_response({"error": "No completed link check runs yet"}, 404)
            return
        run["sites"] = get_link_check_results_for_run(run["id"])
        self._json_response(run)

    def _get_link_check_results(self, run_id_str):
        """Return per-site summaries for a specific run, with run metadata."""
        try:
            run_id = int(run_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id"}, 400)
            return
        runs = get_link_check_runs()
        run_meta = next((r for r in runs if r["id"] == run_id), {"id": run_id})
        run_meta["sites"] = get_link_check_results_for_run(run_id)
        self._json_response(run_meta)

    def _cancel_link_check(self):
        """Request cancellation of the running link check."""
        active = get_active_check()
        if not active:
            self._json_response({"error": "No link check is running"}, 409)
            return
        request_link_check_cancel()
        add_log("LinkChecker", "warn", "Link check cancellation requested")
        self._json_response({"ok": True, "message": "Cancellation requested"})

    def _start_link_check(self, body=None):
        """Kick off a link check run in a background thread."""
        active = get_active_check()
        if active:
            self._json_response({
                "error": "A link check is already running",
                "active_run": active,
            }, 409)
            return

        if body is None:
            body = self._read_body()
        site_ids_filter  = body.get("site_ids")       # list of IDs, or None = all
        check_internal   = body.get("check_internal", True)
        check_external   = body.get("check_external", False)

        # Prefer permanent registry; fall back to live MainWP cache
        registered = get_registered_sites()
        if registered:
            sites = [s for s in registered
                     if s.get("removed_from_mainwp_at") is None]
        else:
            sites = get_cached_sites()

        if not sites:
            self._json_response({"error": "No sites found to check"}, 400)
            return

        if site_ids_filter:
            allowed = {str(x) for x in site_ids_filter}
            sites = [s for s in sites if str(s.get("id")) in allowed]
            if not sites:
                self._json_response(
                    {"error": "None of the selected sites were found"}, 400)
                return

        site_configs = get_all_site_configs()
        run_id = create_link_check_run()
        update_link_check_run_totals(run_id, len(sites))

        t = threading.Thread(
            target=run_link_check,
            args=(sites, add_log, save_link_check_result,
                  finish_link_check_run, run_id),
            kwargs={
                "site_configs":    site_configs,
                "save_site_run_fn": save_link_check_site_run,
                "check_internal":  bool(check_internal),
                "check_external":  bool(check_external),
            },
            daemon=True,
        )
        t.start()

        add_log("LinkChecker", "info",
                f"Link check run #{run_id} started ({len(sites)} sites)")
        self._json_response({"ok": True, "run_id": run_id,
                             "total_sites": len(sites)})
