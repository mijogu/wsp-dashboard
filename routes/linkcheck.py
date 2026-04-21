"""Link checker route handlers."""
import threading

from routes import get_settings, add_log
from db import (
    get_cached_sites,
    get_registered_sites,
    get_all_site_configs,
    create_link_check_run,
    save_link_check_result,
    finish_link_check_run,
    get_link_check_runs,
    get_link_check_results,
    get_latest_link_check_run,
    update_link_check_run_totals,
)
from link_checker import (
    run_link_check,
    get_active_check,
    request_cancel as request_link_check_cancel,
)


class LinkCheckMixin:
    """Mixin for link checker endpoints."""

    def _get_link_check_status(self):
        """Return active run state, or last run info if nothing is running."""
        active = get_active_check()
        if active:
            self._json_response({"active_run": active})
            return
        # No active run — return the most recent run as context
        runs = get_link_check_runs()
        last = runs[0] if runs else None
        self._json_response({"active_run": None, "last_run": last})

    def _get_link_check_runs(self):
        """Return all past link check runs, newest first."""
        self._json_response(get_link_check_runs())

    def _get_latest_link_check(self):
        """Return the most recent completed run with its broken-link results."""
        run = get_latest_link_check_run()
        if not run:
            self._json_response({"error": "No completed link check runs yet"}, 404)
            return
        results = get_link_check_results(run["id"])
        run["results"] = results
        self._json_response(run)

    def _get_link_check_results(self, run_id_str):
        """Return results for a specific run, with run metadata."""
        try:
            run_id = int(run_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id"}, 400)
            return
        results = get_link_check_results(run_id)
        runs = get_link_check_runs()
        run_meta = next((r for r in runs if r["id"] == run_id), {})
        run_meta["results"] = results
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
        # Guard: only one run at a time
        active = get_active_check()
        if active:
            self._json_response({
                "error": "A link check is already running",
                "active_run": active,
            }, 409)
            return

        if body is None:
            body = self._read_body()
        site_ids_filter = body.get("site_ids")  # list of IDs, or None = all

        # Prefer permanent registry; fall back to live MainWP cache
        registered = get_registered_sites()
        if registered:
            # Only check active (non-removed) sites
            sites = [s for s in registered
                     if s.get("removed_from_mainwp_at") is None]
        else:
            sites = get_cached_sites()

        if not sites:
            self._json_response({"error": "No sites found to check"}, 400)
            return

        # Filter to selected sites if caller specified a subset
        if site_ids_filter:
            allowed = {str(x) for x in site_ids_filter}
            sites = [s for s in sites if str(s.get("id")) in allowed]
            if not sites:
                self._json_response(
                    {"error": "None of the selected sites were found"}, 400)
                return

        site_configs = get_all_site_configs()

        run_id = create_link_check_run()
        # Store total_sites so the active_check dict is accurate from the start
        update_link_check_run_totals(run_id, len(sites))

        t = threading.Thread(
            target=run_link_check,
            args=(sites, add_log, save_link_check_result,
                  finish_link_check_run, run_id),
            kwargs={"site_configs": site_configs},
            daemon=True,
        )
        t.start()

        add_log("LinkChecker", "info",
                f"Link check run #{run_id} started ({len(sites)} sites)")
        self._json_response({"ok": True, "run_id": run_id,
                             "total_sites": len(sites)})
