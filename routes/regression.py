"""Regression testing route handlers."""
import os
import threading

from routes import get_settings, add_log
from db import (
    get_cached_sites,
    create_regression_run, save_regression_result,
    finish_regression_run, get_regression_runs,
    get_regression_results, get_latest_regression_run,
    delete_regression_run, get_regression_result_by_id,
    get_all_site_configs,
    get_latest_result_per_site,
    get_results_for_site,
)
from regression import (
    is_available as regression_available,
    get_active_run as get_regression_active_run,
    get_screenshot_dir, run_checks as run_regression_checks,
    request_cancel as request_regression_cancel,
)


class RegressionMixin:
    """Mixin for regression testing endpoints."""

    def _regression_site_status(self):
        """Return latest regression result for every site, one row per (site_id, page_url)."""
        results = get_latest_result_per_site()
        configs = get_all_site_configs()
        for r in results:
            sid = str(r.get("site_id", ""))
            cfg = configs.get(sid, {})
            r["diff_threshold"] = cfg.get("diff_threshold", 1.0)
        self._json_response(results)

    def _regression_site_history(self, site_id_str):
        """Return all regression results for a site, newest first."""
        try:
            site_id = int(site_id_str)
        except ValueError:
            self._json_response({"error": "Invalid site_id"}, 400)
            return
        self._json_response(get_results_for_site(site_id))

    def _regression_status(self):
        """Return whether Playwright is available and if a run is in progress."""
        active = get_regression_active_run()
        self._json_response({
            "available": regression_available(),
            "active_run": active,
        })

    def _regression_runs(self):
        """Return recent regression runs."""
        self._json_response(get_regression_runs())

    def _regression_latest(self):
        """Return the most recent completed run with all its results."""
        run = get_latest_regression_run()
        if run:
            self._json_response(run)
        else:
            self._json_response({"error": "No completed runs yet"}, 404)

    def _regression_results(self, run_id_str):
        """Return results for a specific run, including run metadata."""
        try:
            run_id = int(run_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id"}, 400)
            return
        results = get_regression_results(run_id)
        # Include run-level metadata so the frontend can show timestamps
        runs = get_regression_runs()
        run_meta = next((r for r in runs if r["id"] == run_id), {})
        run_meta["results"] = results
        self._json_response(run_meta)

    def _serve_screenshot(self, filename):
        """Serve a screenshot PNG from the screenshots directory."""
        import mimetypes
        sdir = get_screenshot_dir()
        if not sdir:
            self._json_response({"error": "Screenshots not configured"}, 500)
            return
        # Security: prevent path traversal
        safe = os.path.basename(filename)
        filepath = os.path.join(sdir, safe)
        if not os.path.isfile(filepath):
            self._json_response({"error": "Screenshot not found"}, 404)
            return
        mime = mimetypes.guess_type(filepath)[0] or "image/png"
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _cancel_regression_run(self):
        """Cancel an in-progress regression check and delete its data."""
        active = get_regression_active_run()
        if not active:
            self._json_response({"error": "No regression check is running"}, 409)
            return
        run_id = active.get("run_id")
        request_regression_cancel()
        add_log("Regression", "warn", "Regression check cancellation requested")

        # Schedule cleanup: wait for the run to actually stop, then delete its data
        def _cleanup_after_cancel():
            import time as _time
            for _ in range(30):  # wait up to 60s
                _time.sleep(2)
                if get_regression_active_run() is None:
                    break
            # Now delete the run data and screenshots
            if run_id:
                screenshot_paths = delete_regression_run(run_id)
                sdir = get_screenshot_dir()
                if sdir:
                    for fname in screenshot_paths:
                        fpath = os.path.join(sdir, os.path.basename(fname))
                        try:
                            if os.path.isfile(fpath):
                                os.remove(fpath)
                        except OSError:
                            pass
                add_log("Regression", "info",
                        f"Cancelled run #{run_id} data and screenshots cleaned up")

        threading.Thread(target=_cleanup_after_cancel, daemon=True).start()
        self._json_response({"ok": True, "message": "Cancellation requested — data will be cleaned up"})

    def _delete_regression_run(self, run_id_str):
        """Delete a regression run and its screenshots."""
        try:
            run_id = int(run_id_str)
        except ValueError:
            self._json_response({"error": "Invalid run_id"}, 400)
            return
        # Don't allow deleting an active run
        active = get_regression_active_run()
        if active and active.get("run_id") == run_id:
            self._json_response(
                {"error": "Cannot delete a running check — cancel it first"}, 409)
            return
        # Delete from DB and get screenshot paths for cleanup
        screenshot_paths = delete_regression_run(run_id)
        # Delete screenshot files
        sdir = get_screenshot_dir()
        deleted_files = 0
        if sdir:
            for fname in screenshot_paths:
                fpath = os.path.join(sdir, os.path.basename(fname))
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        deleted_files += 1
                except OSError:
                    pass
        add_log("Regression", "info",
                f"Deleted regression run #{run_id} ({deleted_files} screenshots removed)")
        self._json_response({"ok": True, "deleted_screenshots": deleted_files})

    def _start_regression_run(self, body=None):
        """Kick off a regression check in a background thread."""
        if not regression_available():
            self._json_response({
                "error": "Playwright is not installed. "
                         "Run: pip install playwright && playwright install chromium"
            }, 503)
            return

        # Check if a run is already in progress
        active = get_regression_active_run()
        if active:
            self._json_response({
                "error": "A regression check is already running",
                "active_run": active,
            }, 409)
            return

        # Read optional site_ids filter from POST body
        if body is None:
            body = self._read_body()
        site_ids_filter = body.get("site_ids")  # list of IDs, or None = all

        # Get sites from cache (or MainWP if no cache)
        sites = get_cached_sites()
        if not sites:
            s = get_settings()
            api_key = s.get("mainwp_api_key", "")
            base_url = (s.get("mainwp_url", "").rstrip("/"))
            if not api_key or not base_url:
                self._json_response(
                    {"error": "No sites in cache and MainWP not configured"}, 400)
                return
            json_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            sites, err = self._fetch_all_mainwp_sites(base_url, json_headers)
            if err or not sites:
                self._json_response(
                    {"error": err or "No sites found in MainWP"}, 502)
                return

        # Filter to selected sites if a subset was specified
        if site_ids_filter:
            allowed = set(site_ids_filter)
            # Handle both int and string IDs
            sites = [s for s in sites
                     if s.get("id") in allowed
                     or str(s.get("id")) in {str(x) for x in allowed}]
            if not sites:
                self._json_response(
                    {"error": "None of the selected sites were found"}, 400)
                return

        # Load per-site configs for the run
        site_configs = get_all_site_configs()

        # Create a DB run record
        run_id = create_regression_run()

        # Launch in background thread
        t = threading.Thread(
            target=run_regression_checks,
            args=(sites, add_log, save_regression_result,
                  finish_regression_run, run_id),
            kwargs={"site_configs": site_configs},
            daemon=True,
        )
        t.start()

        add_log("Regression", "info",
                f"Regression run #{run_id} started ({len(sites)} sites)")
        self._json_response({"ok": True, "run_id": run_id,
                             "total_sites": len(sites)})
