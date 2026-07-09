"""Link checker route handlers."""
import hashlib
import re
import threading
from urllib.parse import urlparse

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
    get_link_check_ignore_patterns,
    create_link_check_ignore_pattern,
    update_link_check_ignore_pattern,
    delete_link_check_ignore_pattern,
)
from link_checker import (
    run_link_check,
    get_active_check,
    request_cancel as request_link_check_cancel,
)


_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*(:[0-9]{1,5})?$", re.I)


def _normalize_adhoc_url(raw: str) -> str:
    """Trim, default to https://, and validate an ad-hoc check's input URL."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("URL required")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    netloc = urlparse(raw).netloc
    if not netloc or not _HOSTNAME_RE.match(netloc):
        raise ValueError("Invalid URL")
    return raw


def _adhoc_site_id(normalized_url: str) -> int:
    """
    Deterministic synthetic site_id for an ad-hoc (unregistered) URL — always
    negative so it can never collide with a real MainWP site id. Stable across
    process restarts (uses hashlib, not Python's per-process-randomized
    built-in hash()) and keyed on domain only, so repeat checks of the same
    site (regardless of path/query/www./scheme) land in the same history.
    """
    host = urlparse(normalized_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    digest = hashlib.sha256(host.encode()).hexdigest()
    return -(int(digest[:15], 16) % 2_000_000_000 + 1)


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
        track_redirects  = body.get("track_redirects", False)

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
        ignore_patterns = [p["pattern"] for p in get_link_check_ignore_patterns(enabled_only=True)]
        run_id = create_link_check_run(
            check_internal=bool(check_internal), check_external=bool(check_external),
            track_redirects=bool(track_redirects))
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
                "ignore_patterns": ignore_patterns,
                "track_redirects": bool(track_redirects),
            },
            daemon=True,
        )
        t.start()

        add_log("LinkChecker", "info",
                f"Link check run #{run_id} started ({len(sites)} sites)")
        self._json_response({"ok": True, "run_id": run_id,
                             "total_sites": len(sites)})

    def _start_adhoc_link_check(self, body=None):
        """
        Kick off a link check against a single URL that isn't registered in
        MainWP — e.g. a prospective client's site during due diligence.
        Reuses run_link_check() unchanged; the only difference from a normal
        run is a single synthetic site instead of a registry sweep, and
        always checking both internal + external links and tracking
        successful redirects (a due-diligence audit should be thorough by
        default, regardless of the Options modal's persisted scope toggles).
        """
        active = get_active_check()
        if active:
            self._json_response({
                "error": "A link check is already running",
                "active_run": active,
            }, 409)
            return

        if body is None:
            body = self._read_body()
        try:
            url = _normalize_adhoc_url(body.get("url"))
        except ValueError as e:
            self._json_response({"error": str(e)}, 400)
            return

        site_id = _adhoc_site_id(url)
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        name = (body.get("name") or "").strip() or domain
        sites = [{"id": site_id, "name": name, "url": url}]

        ignore_patterns = [p["pattern"] for p in get_link_check_ignore_patterns(enabled_only=True)]
        run_id = create_link_check_run(check_internal=True, check_external=True,
                                        is_adhoc=True, track_redirects=True)
        update_link_check_run_totals(run_id, 1)

        t = threading.Thread(
            target=run_link_check,
            args=(sites, add_log, save_link_check_result,
                  finish_link_check_run, run_id),
            kwargs={
                "site_configs":    None,
                "save_site_run_fn": save_link_check_site_run,
                "check_internal":  True,
                "check_external":  True,
                "ignore_patterns": ignore_patterns,
                "track_redirects": True,
            },
            daemon=True,
        )
        t.start()

        add_log("LinkChecker", "info", f"Ad-hoc link check run #{run_id} started ({url})")
        self._json_response({"ok": True, "run_id": run_id, "total_sites": 1})

    # ─── Ignore patterns (settings) ──────────────────────────────

    def _get_link_check_ignore_patterns(self):
        self._json_response(get_link_check_ignore_patterns())

    def _create_link_check_ignore_pattern(self, body):
        pattern = (body.get("pattern") or "").strip()
        if not pattern:
            self._json_response({"error": "pattern required"}, 400)
            return
        note = (body.get("note") or "").strip()
        pattern_id = create_link_check_ignore_pattern(pattern, note)
        add_log("LinkChecker", "ok", f"Ignore pattern added: {pattern}")
        self._json_response({"ok": True, "id": pattern_id})

    def _update_link_check_ignore_pattern(self, pattern_id_str, body):
        try:
            pattern_id = int(pattern_id_str)
        except ValueError:
            self._json_response({"error": "Invalid pattern id"}, 400)
            return
        allowed = {"pattern", "note", "enabled"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if "enabled" in updates:
            updates["enabled"] = int(bool(updates["enabled"]))
        update_link_check_ignore_pattern(pattern_id, **updates)
        self._json_response({"ok": True})

    def _delete_link_check_ignore_pattern(self, pattern_id_str):
        try:
            pattern_id = int(pattern_id_str)
        except ValueError:
            self._json_response({"error": "Invalid pattern id"}, 400)
            return
        delete_link_check_ignore_pattern(pattern_id)
        add_log("LinkChecker", "ok", f"Ignore pattern deleted: {pattern_id}")
        self._json_response({"ok": True})
