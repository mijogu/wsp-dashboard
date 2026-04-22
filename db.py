"""
WP Maintenance Dashboard — SQLite persistence layer.

Stores:
  - sites: permanent site registry — never deleted, marked when removed from MainWP
  - update_history: individual plugin/theme/WP update records from Pro Reports
  - fetch_log: metadata about each fetch (when, how many records, date range)
  - sites_cache: snapshot of MainWP site info for fast dashboard startup

DB file lives next to server.py as `dashboard.db`.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")

# Thread-local storage for connections (SQLite connections aren't thread-safe)
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local DB connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")  # better concurrency
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = _get_conn()
    conn.executescript("""
        -- Permanent site registry: never deleted, survives MainWP removal
        CREATE TABLE IF NOT EXISTS sites (
            id                      INTEGER PRIMARY KEY,  -- MainWP site ID (stable)
            name                    TEXT NOT NULL,
            url                     TEXT,
            added_at                TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at            TEXT,                 -- last successful MainWP sync
            removed_from_mainwp_at  TEXT                  -- NULL = still active
        );

        CREATE TABLE IF NOT EXISTS update_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id         TEXT NOT NULL,
            site_name       TEXT NOT NULL,
            site_url        TEXT,
            update_type     TEXT NOT NULL,       -- plugins, themes, wordpress
            name            TEXT,                -- plugin/theme/WP name
            old_version     TEXT,
            new_version     TEXT,
            updated_date    TEXT,                -- human-readable date
            updated_time    TEXT,                -- human-readable time
            updated_utime   TEXT,                -- sortable timestamp
            updated_author  TEXT,
            updated_slug    TEXT,
            raw_fields      TEXT,                -- full JSON of all fields
            fetch_id        INTEGER,             -- which fetch produced this row
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(site_id, update_type, name, updated_utime)
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at      TEXT DEFAULT (datetime('now')),
            date_from       TEXT NOT NULL,
            date_to         TEXT NOT NULL,
            days            INTEGER NOT NULL,
            sites_queried   INTEGER NOT NULL,
            records_found   INTEGER NOT NULL,
            records_new     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sites_cache (
            id              INTEGER PRIMARY KEY,  -- MainWP site id
            name            TEXT NOT NULL,
            url             TEXT,
            data            TEXT,                  -- full JSON snapshot
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_history_site
            ON update_history(site_id);
        CREATE INDEX IF NOT EXISTS idx_history_type
            ON update_history(update_type);
        CREATE INDEX IF NOT EXISTS idx_history_utime
            ON update_history(updated_utime);
        CREATE INDEX IF NOT EXISTS idx_history_name
            ON update_history(name);

        -- Regression testing tables
        CREATE TABLE IF NOT EXISTS regression_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT DEFAULT 'running',  -- running, completed, failed
            total_sites     INTEGER DEFAULT 0,
            checked         INTEGER DEFAULT 0,
            issues_found    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS regression_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            site_id         INTEGER,
            site_name       TEXT,
            site_url        TEXT,
            http_status     INTEGER,
            load_time_ms    INTEGER,
            js_errors       TEXT,       -- JSON array
            broken_resources TEXT,      -- JSON array of {url, status}
            screenshot_path TEXT,
            has_issues      INTEGER DEFAULT 0,
            error           TEXT,
            checked_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES regression_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_regression_run
            ON regression_results(run_id);

        -- Per-site configuration
        CREATE TABLE IF NOT EXISTS site_config (
            site_id     TEXT PRIMARY KEY,
            client_name TEXT,
            notes       TEXT,
            test_pages  TEXT DEFAULT '[]',  -- JSON array of full URLs to test
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Link checker tables
        CREATE TABLE IF NOT EXISTS link_check_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at          TEXT NOT NULL,
            finished_at         TEXT,
            status              TEXT DEFAULT 'running',
            total_sites         INTEGER DEFAULT 0,
            total_pages_crawled INTEGER DEFAULT 0,
            total_links_checked INTEGER DEFAULT 0,
            total_broken        INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS link_check_results (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        INTEGER NOT NULL,
            site_id       INTEGER,
            site_name     TEXT,
            site_url      TEXT,
            source_page   TEXT,
            link_url      TEXT,
            status_code   INTEGER,
            redirect_url  TEXT,
            is_broken     INTEGER DEFAULT 0,
            is_external   INTEGER DEFAULT 0,
            error         TEXT,
            checked_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES link_check_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_lc_results_run
            ON link_check_results(run_id);
        CREATE INDEX IF NOT EXISTS idx_lc_results_site
            ON link_check_results(run_id, site_id);

        -- Per-site summary row written after every site completes, even with 0 broken
        CREATE TABLE IF NOT EXISTS link_check_site_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            site_id         INTEGER,
            site_name       TEXT,
            site_url        TEXT,
            pages_crawled   INTEGER DEFAULT 0,
            links_checked   INTEGER DEFAULT 0,
            broken_count    INTEGER DEFAULT 0,
            FOREIGN KEY (run_id) REFERENCES link_check_runs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_lcsr_run_id  ON link_check_site_runs(run_id);
        CREATE INDEX IF NOT EXISTS idx_lcsr_site_id ON link_check_site_runs(site_id);

        -- Visual regression baselines: one screenshot per (site_id, page_url)
        CREATE TABLE IF NOT EXISTS baseline_screenshots (
            site_id         TEXT NOT NULL,
            page_url        TEXT NOT NULL,
            screenshot_path TEXT NOT NULL,
            run_id          INTEGER NOT NULL,
            set_at          TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (site_id, page_url)
        );

        -- Onboarding: dynamic field definitions
        CREATE TABLE IF NOT EXISTS onboarding_fields (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            group_name    TEXT NOT NULL DEFAULT 'General',
            field_type    TEXT NOT NULL DEFAULT 'text',
            options       TEXT NOT NULL DEFAULT '[]',
            position      INTEGER NOT NULL DEFAULT 0,
            hidden        INTEGER NOT NULL DEFAULT 0,
            default_value TEXT NOT NULL DEFAULT ''
        );

        -- Onboarding: per-site cell values
        CREATE TABLE IF NOT EXISTS onboarding_data (
            site_id    INTEGER NOT NULL,
            field_id   TEXT NOT NULL,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (site_id, field_id)
        );

        -- Heartbeat scan tables
        CREATE TABLE IF NOT EXISTS heartbeat_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL DEFAULT 'running',
            total_sites INTEGER NOT NULL DEFAULT 0,
            checked     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS heartbeat_results (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              INTEGER NOT NULL,
            site_id             INTEGER NOT NULL,
            site_url            TEXT NOT NULL,
            checked_at          TEXT NOT NULL,
            dns_json            TEXT,
            spf_status          TEXT,
            spf_record          TEXT,
            dkim_status         TEXT,
            dkim_selector       TEXT,
            dmarc_status        TEXT,
            dmarc_record        TEXT,
            smtp_status         TEXT,
            smtp_detail         TEXT,
            robots_status       TEXT,
            robots_version      TEXT,
            robots_content      TEXT,
            sitemap_status      TEXT,
            sitemap_url         TEXT,
            staging_status      TEXT,
            staging_final_url   TEXT,
            staging_auth_status TEXT,
            wp_api_status       TEXT,
            rdap_status         TEXT,
            rdap_registrar      TEXT,
            rdap_expires_at     TEXT,
            rdap_created_at     TEXT,
            rdap_status_flags   TEXT,
            rdap_nameservers    TEXT,
            rdap_json           TEXT,
            error               TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_heartbeat_results_site
            ON heartbeat_results(site_id, checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_heartbeat_results_run
            ON heartbeat_results(run_id);
    """)
    conn.commit()

    seed_onboarding_fields()

    # Migrations: add columns that may not exist in older DBs
    for migration in [
        "ALTER TABLE regression_results ADD COLUMN page_url TEXT",
        "ALTER TABLE regression_results ADD COLUMN diff_score REAL",
        "ALTER TABLE regression_results ADD COLUMN diff_screenshot_path TEXT",
        "ALTER TABLE site_config ADD COLUMN diff_threshold REAL DEFAULT 1.0",
        "ALTER TABLE regression_results ADD COLUMN prev_screenshot_path TEXT",
        # Link checker v2 — stats columns
        "ALTER TABLE link_check_site_runs ADD COLUMN external_count INTEGER DEFAULT 0",
        "ALTER TABLE link_check_site_runs ADD COLUMN redirect_count INTEGER DEFAULT 0",
        "ALTER TABLE link_check_site_runs ADD COLUMN image_link_count INTEGER DEFAULT 0",
        "ALTER TABLE link_check_results ADD COLUMN is_image INTEGER DEFAULT 0",
        # Onboarding
        "ALTER TABLE site_config ADD COLUMN hidden_from_onboarding INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # Column already exists


# ─── Update History ────────────────────────────────────────────


def save_update_records(records: list, date_from: str, date_to: str,
                        days: int, sites_queried: int) -> dict:
    """
    Save update history records to DB. Uses UPSERT to avoid duplicates.
    Returns stats: {total, new, duplicate}.
    """
    conn = _get_conn()

    # Create a fetch_log entry
    cur = conn.execute(
        "INSERT INTO fetch_log (date_from, date_to, days, sites_queried, records_found) "
        "VALUES (?, ?, ?, ?, ?)",
        (date_from, date_to, days, sites_queried, len(records))
    )
    fetch_id = cur.lastrowid

    new_count = 0
    dup_count = 0

    for rec in records:
        # Extract known fields, store the rest in raw_fields
        site_id = str(rec.get("_site_id", ""))
        site_name = rec.get("_site_name", "")
        site_url = rec.get("_site_url", "")
        update_type = rec.get("_update_type", "")
        name = rec.get("name", "") or rec.get("title", "")
        old_version = rec.get("old_version", "")
        new_version = rec.get("current_version", "") or rec.get("new_version", "")
        updated_date = rec.get("updated_date", "")
        updated_time = rec.get("updated_time", "")
        updated_utime = rec.get("updated_utime", "")
        updated_author = rec.get("updated_author", "")
        updated_slug = rec.get("updated_slug", "")

        # raw_fields = everything except internal tags
        raw = {k: v for k, v in rec.items() if not k.startswith("_")}

        try:
            cur = conn.execute(
                "INSERT INTO update_history "
                "(site_id, site_name, site_url, update_type, name, old_version, "
                " new_version, updated_date, updated_time, updated_utime, "
                " updated_author, updated_slug, raw_fields, fetch_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(site_id, update_type, name, updated_utime) DO NOTHING",
                (site_id, site_name, site_url, update_type, name, old_version,
                 new_version, updated_date, updated_time, updated_utime,
                 updated_author, updated_slug, json.dumps(raw), fetch_id)
            )
            # rowcount is 1 if the row was inserted, 0 if DO NOTHING fired
            if cur.rowcount == 1:
                new_count += 1
            else:
                dup_count += 1
        except sqlite3.IntegrityError:
            dup_count += 1

    # Update fetch_log with new count
    conn.execute(
        "UPDATE fetch_log SET records_new = ? WHERE id = ?",
        (new_count, fetch_id)
    )
    conn.commit()

    return {"total": len(records), "new": new_count, "duplicate": dup_count,
            "fetch_id": fetch_id}


def get_update_history(days: int = None, update_type: str = None,
                       site_id: str = None, limit: int = 5000) -> list:
    """
    Query stored update history. All filters are optional.
    Returns list of dicts, newest first.
    """
    conn = _get_conn()
    conditions = []
    params = []

    if days:
        conditions.append("updated_utime >= date('now', ?)")
        params.append(f"-{days} days")
    if update_type and update_type != "all":
        conditions.append("update_type = ?")
        params.append(update_type)
    if site_id:
        conditions.append("site_id = ?")
        params.append(str(site_id))

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM update_history {where} "
        f"ORDER BY updated_utime DESC LIMIT ?",
        params
    ).fetchall()

    return [dict(r) for r in rows]


def get_history_stats() -> dict:
    """Quick stats about what's in the DB."""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM update_history").fetchone()[0]
    sites = conn.execute(
        "SELECT COUNT(DISTINCT site_id) FROM update_history"
    ).fetchone()[0]

    oldest = conn.execute(
        "SELECT MIN(updated_utime) FROM update_history"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(updated_utime) FROM update_history"
    ).fetchone()[0]

    last_fetch = conn.execute(
        "SELECT fetched_at, records_found, records_new FROM fetch_log "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return {
        "total_records": total,
        "unique_sites": sites,
        "oldest_record": oldest,
        "newest_record": newest,
        "last_fetch": dict(last_fetch) if last_fetch else None,
    }


def get_last_fetch_date() -> str | None:
    """
    Return the date_to of the most recent fetch_log entry.
    Used to compute default start_date for incremental syncs.
    Returns ISO date string (YYYY-MM-DD) or None if no fetches yet.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT date_to FROM fetch_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


# ─── Sites Cache ───────────────────────────────────────────────


def cache_sites(sites: list):
    """Cache site data from MainWP for fast startup."""
    conn = _get_conn()
    for site in sites:
        site_id = site.get("id")
        if not site_id:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO sites_cache (id, name, url, data, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (int(site_id), site.get("name", ""), site.get("url", ""),
             json.dumps(site))
        )
    conn.commit()


def get_cached_sites() -> list:
    """Get cached site data. Returns empty list if no cache."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT data FROM sites_cache ORDER BY name"
    ).fetchall()
    results = []
    for r in rows:
        try:
            results.append(json.loads(r["data"]))
        except (json.JSONDecodeError, KeyError):
            pass
    return results


def get_cache_age() -> str | None:
    """How old is the sites cache? Returns ISO timestamp or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MIN(updated_at) FROM sites_cache"
    ).fetchone()
    return row[0] if row else None


# ─── Site Registry ───────────────────────────────────────────


def upsert_sites(sites: list):
    """Permanently register sites from a full MainWP sync.

    - Inserts new sites, updates name/url/last_seen_at for existing ones.
    - Clears removed_from_mainwp_at when a previously-removed site comes back.
    - Marks any site not in this sync as removed (if not already marked).
    Never deletes rows — historical data always has a named identity.
    """
    conn = _get_conn()
    incoming_ids = set()

    for site in sites:
        site_id = site.get("id")
        if not site_id:
            continue
        site_id = int(site_id)
        incoming_ids.add(site_id)
        conn.execute("""
            INSERT INTO sites (id, name, url, added_at, last_seen_at, removed_from_mainwp_at)
            VALUES (?, ?, ?, datetime('now'), datetime('now'), NULL)
            ON CONFLICT(id) DO UPDATE SET
                name                   = excluded.name,
                url                    = excluded.url,
                last_seen_at           = excluded.last_seen_at,
                removed_from_mainwp_at = NULL
        """, (site_id, site.get("name", ""), site.get("url", "")))

    # Mark any previously-active site that didn't appear in this sync
    if incoming_ids:
        placeholders = ",".join("?" * len(incoming_ids))
        conn.execute(f"""
            UPDATE sites
            SET removed_from_mainwp_at = datetime('now')
            WHERE id NOT IN ({placeholders})
              AND removed_from_mainwp_at IS NULL
        """, list(incoming_ids))

    conn.commit()


def get_registered_sites() -> list:
    """Return all ever-seen sites, active first then removed, both alphabetical."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, name, url, added_at, last_seen_at, removed_from_mainwp_at
        FROM sites
        ORDER BY
            CASE WHEN removed_from_mainwp_at IS NULL THEN 0 ELSE 1 END,
            name COLLATE NOCASE
    """).fetchall()
    return [dict(r) for r in rows]


# ─── Regression Testing ──────────────────────────────────────


def create_regression_run() -> int:
    """Create a new regression run record. Returns the run_id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO regression_runs (started_at) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    conn.commit()
    return cur.lastrowid


def save_regression_result(run_id: int, result: dict):
    """Save a single site's regression check result."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO regression_results "
        "(run_id, site_id, site_name, site_url, page_url, http_status, load_time_ms, "
        " js_errors, broken_resources, screenshot_path, has_issues, error, "
        " diff_score, diff_screenshot_path, prev_screenshot_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, result["site_id"], result["site_name"], result["site_url"],
         result.get("page_url"),
         result["http_status"], result["load_time_ms"],
         result["js_errors"], result["broken_resources"],
         result["screenshot_path"], result["has_issues"],
         result.get("error"),
         result.get("diff_score"), result.get("diff_screenshot_path"),
         result.get("prev_screenshot_path"))
    )
    # Update the run's checked count
    conn.execute(
        "UPDATE regression_runs SET checked = checked + 1 WHERE id = ?",
        (run_id,)
    )
    conn.commit()


def finish_regression_run(run_id: int, total: int, issues: int,
                          status: str = "completed"):
    """Mark a regression run as finished."""
    conn = _get_conn()
    conn.execute(
        "UPDATE regression_runs "
        "SET finished_at = ?, status = ?, total_sites = ?, issues_found = ? "
        "WHERE id = ?",
        (datetime.now().isoformat(), status, total, issues, run_id)
    )
    conn.commit()


def get_regression_runs(limit: int = 20) -> list:
    """Get recent regression runs, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM regression_runs ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_regression_results(run_id: int) -> list:
    """Get all results for a specific run."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM regression_results WHERE run_id = ? ORDER BY site_name",
        (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_regression_run(run_id: int) -> list:
    """Delete a regression run and its results. Returns all image paths for cleanup."""
    conn = _get_conn()
    # Collect screenshot and diff image filenames before deleting
    rows = conn.execute(
        "SELECT screenshot_path, diff_screenshot_path FROM regression_results "
        "WHERE run_id = ?",
        (run_id,)
    ).fetchall()
    image_paths = []
    for r in rows:
        if r[0]:
            image_paths.append(r[0])
        if r[1]:
            image_paths.append(r[1])
    # Delete results then the run
    conn.execute("DELETE FROM regression_results WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM regression_runs WHERE id = ?", (run_id,))
    conn.commit()
    return image_paths


def get_latest_regression_run() -> dict | None:
    """Get the most recent completed regression run with its results."""
    conn = _get_conn()
    run = conn.execute(
        "SELECT * FROM regression_runs "
        "WHERE status IN ('completed', 'failed') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not run:
        return None
    run_dict = dict(run)
    run_dict["results"] = get_regression_results(run_dict["id"])
    return run_dict


def get_latest_result_per_site() -> list:
    """Return the most recent regression result for each (site_id, page_url) pair."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT rr.*, runs.started_at AS run_started_at, runs.status AS run_status
        FROM regression_results rr
        INNER JOIN (
            SELECT site_id, COALESCE(page_url, '') AS norm_page,
                   MAX(id) AS max_id
            FROM regression_results
            GROUP BY site_id, norm_page
        ) latest ON rr.id = latest.max_id
        LEFT JOIN regression_runs runs ON rr.run_id = runs.id
        ORDER BY rr.site_name, rr.page_url
    """).fetchall()
    return [dict(r) for r in rows]


# ─── Site Configuration ────────────────────────────────────────


def get_site_config(site_id) -> dict:
    """Get config for a single site. Returns empty dict with defaults if not set."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM site_config WHERE site_id = ?", (str(site_id),)
    ).fetchone()
    if row:
        return dict(row)
    return {"site_id": str(site_id), "client_name": None, "notes": None,
            "test_pages": "[]", "diff_threshold": 1.0,
            "hidden_from_onboarding": 0, "updated_at": None}


def save_site_config(site_id, client_name: str = None, notes: str = None,
                     test_pages: str = "[]", diff_threshold: float = None,
                     hidden_from_onboarding: int = None) -> None:
    """Insert or replace config for a site."""
    conn = _get_conn()
    threshold = diff_threshold if diff_threshold is not None else 1.0
    # Read existing value for hidden_from_onboarding to avoid overwriting if not provided
    if hidden_from_onboarding is None:
        existing = conn.execute(
            "SELECT hidden_from_onboarding FROM site_config WHERE site_id = ?",
            (str(site_id),)
        ).fetchone()
        hidden_from_onboarding = existing["hidden_from_onboarding"] if existing else 0
    conn.execute(
        "INSERT INTO site_config "
        "(site_id, client_name, notes, test_pages, diff_threshold, hidden_from_onboarding, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(site_id) DO UPDATE SET "
        "  client_name = excluded.client_name, "
        "  notes = excluded.notes, "
        "  test_pages = excluded.test_pages, "
        "  diff_threshold = excluded.diff_threshold, "
        "  hidden_from_onboarding = excluded.hidden_from_onboarding, "
        "  updated_at = excluded.updated_at",
        (str(site_id), client_name or None, notes or None, test_pages or "[]",
         threshold, int(hidden_from_onboarding))
    )
    conn.commit()


def get_all_site_configs() -> dict:
    """Return all site configs as a dict keyed by site_id string."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM site_config").fetchall()
    return {str(r["site_id"]): dict(r) for r in rows}


# ─── Visual Regression Baselines ───────────────────────────────


def get_baseline(site_id, page_url: str) -> dict | None:
    """Get the current baseline for a (site_id, page_url) pair, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM baseline_screenshots WHERE site_id = ? AND page_url = ?",
        (str(site_id), page_url)
    ).fetchone()
    return dict(row) if row else None


def set_baseline(site_id, page_url: str, screenshot_path: str, run_id: int) -> None:
    """Promote a screenshot to baseline for a (site_id, page_url) pair."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO baseline_screenshots (site_id, page_url, screenshot_path, run_id, set_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(site_id, page_url) DO UPDATE SET "
        "  screenshot_path = excluded.screenshot_path, "
        "  run_id = excluded.run_id, "
        "  set_at = excluded.set_at",
        (str(site_id), page_url, screenshot_path, run_id)
    )
    conn.commit()


def get_all_baselines() -> dict:
    """Return all baselines as a nested dict: {site_id: {page_url: baseline_dict}}."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM baseline_screenshots").fetchall()
    result: dict = {}
    for r in rows:
        sid = str(r["site_id"])
        if sid not in result:
            result[sid] = {}
        result[sid][r["page_url"]] = dict(r)
    return result


def get_regression_result_by_id(result_id: int) -> dict | None:
    """Get a single regression result row by its primary key."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM regression_results WHERE id = ?", (result_id,)
    ).fetchone()
    return dict(row) if row else None


def get_previous_screenshot(site_id, page_url: str, current_run_id: int) -> str | None:
    """Return screenshot_path from the most recent prior result for (site_id, page_url).

    NOTE: baseline_screenshots table is no longer written by active code — it can be
    dropped in a future migration once the transition window has passed.
    """
    conn = _get_conn()
    row = conn.execute("""
        SELECT screenshot_path FROM regression_results
        WHERE site_id = ?
          AND COALESCE(page_url, '') = COALESCE(?, '')
          AND run_id < ?
          AND screenshot_path IS NOT NULL
        ORDER BY id DESC LIMIT 1
    """, (site_id, page_url, current_run_id)).fetchone()
    return row["screenshot_path"] if row else None


def get_results_for_site(site_id) -> list:
    """Return all regression results for a site, newest first."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT rr.*, runs.started_at AS run_started_at
        FROM regression_results rr
        LEFT JOIN regression_runs runs ON rr.run_id = runs.id
        WHERE rr.site_id = ?
        ORDER BY rr.id DESC
    """, (site_id,)).fetchall()
    return [dict(r) for r in rows]


# ─── Link Checker ────────────────────────────────────────────


def create_link_check_run() -> int:
    """Create a new link check run. Returns the run_id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO link_check_runs (started_at) VALUES (?)",
        (datetime.utcnow().isoformat(),)
    )
    conn.commit()
    return cur.lastrowid


def save_link_check_result(run_id: int, result: dict):
    """Save a single link check result row (call for every broken link found)."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO link_check_results "
        "(run_id, site_id, site_name, site_url, source_page, link_url, "
        " status_code, redirect_url, is_broken, is_external, is_image, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            result.get("site_id"),
            result.get("site_name"),
            result.get("site_url"),
            result.get("source_page"),
            result.get("link_url"),
            result.get("status_code"),
            result.get("redirect_url"),
            1 if result.get("is_broken") else 0,
            1 if result.get("is_external") else 0,
            1 if result.get("is_image")    else 0,
            result.get("error"),
        )
    )
    conn.commit()


def finish_link_check_run(run_id: int, pages_crawled: int, links_checked: int,
                          broken_count: int, status: str = "completed"):
    """Mark a link check run as finished."""
    conn = _get_conn()
    conn.execute(
        "UPDATE link_check_runs SET finished_at=?, status=?, "
        "total_pages_crawled=?, total_links_checked=?, total_broken=? "
        "WHERE id=?",
        (datetime.utcnow().isoformat(), status,
         pages_crawled, links_checked, broken_count, run_id)
    )
    conn.commit()


def get_link_check_runs() -> list:
    """Return all link check runs, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM link_check_runs ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_link_check_results(run_id: int) -> list:
    """Return all broken link results for a run."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM link_check_results WHERE run_id = ? ORDER BY site_id, source_page, link_url",
        (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_link_check_run() -> dict | None:
    """Return the most recent completed link check run, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM link_check_runs WHERE status='completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def update_link_check_run_totals(run_id: int, total_sites: int):
    """Update the total_sites count once all sites are known."""
    conn = _get_conn()
    conn.execute(
        "UPDATE link_check_runs SET total_sites=? WHERE id=?",
        (total_sites, run_id)
    )
    conn.commit()


def save_link_check_site_run(run_id: int, site_id, site_name: str,
                              site_url: str, pages_crawled: int,
                              links_checked: int, broken_count: int,
                              *, external_count: int = 0,
                              redirect_count: int = 0,
                              image_link_count: int = 0):
    """Save a per-site summary row after each site finishes being checked."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO link_check_site_runs "
        "(run_id, site_id, site_name, site_url, pages_crawled, links_checked, "
        " broken_count, external_count, redirect_count, image_link_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, site_id, site_name, site_url, pages_crawled, links_checked,
         broken_count, external_count, redirect_count, image_link_count)
    )
    conn.commit()


def get_link_check_site_status() -> list:
    """
    Return per-site summary from each site's own most recent completed run,
    plus prev_broken_count / prev_links_checked for delta badges.

    Each site may come from a different run_id (e.g. if the last run only
    checked a subset of sites, others still show their own latest data).
    """
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT sr.site_id, sr.site_name, sr.site_url,
               sr.pages_crawled, sr.links_checked, sr.broken_count,
               sr.external_count, sr.redirect_count, sr.image_link_count,
               sr.run_id,
               r.started_at AS run_started_at,
               prev.links_checked AS prev_links_checked,
               prev.broken_count  AS prev_broken_count
        FROM link_check_site_runs sr
        JOIN link_check_runs r ON r.id = sr.run_id AND r.status = 'completed'
        -- Only the most recent completed run for each site
        JOIN (
            SELECT sr2.site_id, MAX(sr2.run_id) AS max_run_id
            FROM link_check_site_runs sr2
            JOIN link_check_runs r2 ON r2.id = sr2.run_id AND r2.status = 'completed'
            GROUP BY sr2.site_id
        ) latest ON latest.site_id = sr.site_id AND latest.max_run_id = sr.run_id
        -- Previous completed run for each site (for delta badges)
        LEFT JOIN link_check_site_runs prev
               ON prev.site_id = sr.site_id
              AND prev.run_id = (
                  SELECT MAX(sr3.run_id)
                  FROM link_check_site_runs sr3
                  JOIN link_check_runs r3 ON r3.id = sr3.run_id
                  WHERE sr3.site_id = sr.site_id
                    AND sr3.run_id < sr.run_id
                    AND r3.status = 'completed'
              )
        ORDER BY sr.broken_count DESC, sr.site_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_link_check_site_history(site_id: int) -> list:
    """
    Return per-run summary for one site, newest first, with prev_broken_count
    and prev_links_checked from the immediately preceding run for delta badges.
    """
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT run_id, started_at, finished_at, status,
               pages_crawled, links_checked, broken_count,
               external_count, redirect_count, image_link_count,
               prev_links_checked, prev_broken_count
        FROM (
            SELECT sr.run_id, r.started_at, r.finished_at, r.status,
                   sr.pages_crawled, sr.links_checked, sr.broken_count,
                   sr.external_count, sr.redirect_count, sr.image_link_count,
                   LAG(sr.links_checked) OVER (ORDER BY sr.run_id ASC) AS prev_links_checked,
                   LAG(sr.broken_count)  OVER (ORDER BY sr.run_id ASC) AS prev_broken_count
            FROM link_check_site_runs sr
            JOIN link_check_runs r ON r.id = sr.run_id
            WHERE sr.site_id = ? AND r.status = 'completed'
        )
        ORDER BY run_id DESC
        """,
        (site_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_link_check_run_site_results(run_id: int, site_id: int) -> list:
    """Return all broken link results for one site within one run."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM link_check_results "
        "WHERE run_id=? AND site_id=? "
        "ORDER BY source_page, link_url",
        (run_id, site_id)
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Onboarding ──────────────────────────────────────────────


_DEFAULT_ONBOARDING_FIELDS = [
    ("hosting_provider",  "Hosting Provider",         "Infrastructure", "text",  0),
    ("domain_registrar",  "Domain Registrar",         "Infrastructure", "text",  1),
    ("dns_host",          "DNS Host",                 "Infrastructure", "text",  2),
    ("email_host",        "Email Host",               "Infrastructure", "text",  3),
    ("staging_url",       "Staging URL",              "Staging",        "url",   4),
    ("staging_basic_auth","Basic Auth",               "Staging",        "bool",  5),
    ("cloudflare_active", "Cloudflare Active",        "Security",       "bool",  6),
    ("spf",               "SPF",                      "Security",       "bool",  7),
    ("dkim",              "DKIM",                     "Security",       "bool",  8),
    ("dmarc",             "DMARC",                    "Security",       "bool",  9),
    ("two_fa",            "2FA Enabled",              "Security",       "bool",  10),
    ("wordfence",         "Wordfence",                "Security",       "bool",  11),
    ("cleantalk",         "CleanTalk",                "Security",       "bool",  12),
    ("uptime_robot",      "Uptime Robot",             "Monitoring",     "bool",  13),
    ("gsc_access",        "GSC Access",               "Monitoring",     "bool",  14),
    ("ga_connected",      "GA Connected",             "Monitoring",     "bool",  15),
    ("local_backups",     "Local Backups",            "Backups",        "bool",  16),
    ("aws_backups",       "AWS Backups",              "Backups",        "bool",  17),
    ("hosting_billing",   "Hosting Billing Contact",  "Billing",        "text",  18),
    ("domain_billing",    "Domain Billing Contact",   "Billing",        "text",  19),
]


def seed_onboarding_fields() -> None:
    """Insert default onboarding fields if they don't already exist."""
    conn = _get_conn()
    for fid, name, group, ftype, pos in _DEFAULT_ONBOARDING_FIELDS:
        conn.execute(
            "INSERT OR IGNORE INTO onboarding_fields "
            "(id, name, group_name, field_type, position) VALUES (?, ?, ?, ?, ?)",
            (fid, name, group, ftype, pos)
        )
    conn.commit()


def get_onboarding_fields() -> list:
    """Return all onboarding fields ordered by position."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM onboarding_fields ORDER BY position, id"
    ).fetchall()
    return [dict(r) for r in rows]


def create_onboarding_field(fid: str, name: str, group_name: str,
                             field_type: str, options: str = "[]",
                             default_value: str = "") -> None:
    """Insert a new onboarding field at the end of its group."""
    conn = _get_conn()
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM onboarding_fields"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO onboarding_fields (id, name, group_name, field_type, options, default_value, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, name, group_name, field_type, options, default_value, max_pos + 1)
    )
    conn.commit()


def update_onboarding_field(fid: str, **kwargs) -> None:
    """Update one or more columns on an onboarding field."""
    allowed = {"name", "group_name", "field_type", "options", "hidden",
               "position", "default_value"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE onboarding_fields SET {set_clause} WHERE id = ?",
        list(updates.values()) + [fid]
    )
    conn.commit()


def delete_onboarding_field(fid: str) -> None:
    """Delete a field and all its cell data."""
    conn = _get_conn()
    conn.execute("DELETE FROM onboarding_data WHERE field_id = ?", (fid,))
    conn.execute("DELETE FROM onboarding_fields WHERE id = ?", (fid,))
    conn.commit()


def get_onboarding_data() -> dict:
    """Return all onboarding cell values as {site_id_str: {field_id: value}}."""
    conn = _get_conn()
    rows = conn.execute("SELECT site_id, field_id, value FROM onboarding_data").fetchall()
    result: dict = {}
    for r in rows:
        sid = str(r["site_id"])
        if sid not in result:
            result[sid] = {}
        result[sid][r["field_id"]] = r["value"]
    return result


def save_onboarding_cell(site_id: int, field_id: str, value: str) -> None:
    """Upsert a single onboarding cell."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO onboarding_data (site_id, field_id, value, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(site_id, field_id) DO UPDATE SET "
        "  value = excluded.value, updated_at = excluded.updated_at",
        (int(site_id), field_id, value)
    )
    conn.commit()


def get_onboarding_cell(site_id: int, field_id: str) -> str:
    """Return the value of a single onboarding cell, or '' if not set."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT value FROM onboarding_data WHERE site_id=? AND field_id=?",
        (int(site_id), field_id)
    ).fetchone()
    return row["value"] if row else ""


def get_link_check_results_for_run(run_id: int) -> list:
    """
    Return per-site summary + broken results for one run, with
    prev_broken_count / prev_links_checked from the preceding completed
    run for each site (for delta badges in the UI).
    """
    conn = _get_conn()
    sites = conn.execute(
        """
        SELECT sr.id, sr.run_id, sr.site_id, sr.site_name, sr.site_url,
               sr.pages_crawled, sr.links_checked, sr.broken_count,
               sr.external_count, sr.redirect_count, sr.image_link_count,
               prev.links_checked AS prev_links_checked,
               prev.broken_count  AS prev_broken_count
        FROM link_check_site_runs sr
        LEFT JOIN link_check_site_runs prev
               ON prev.site_id = sr.site_id
              AND prev.run_id = (
                  SELECT MAX(sr2.run_id)
                  FROM link_check_site_runs sr2
                  JOIN link_check_runs r2 ON r2.id = sr2.run_id
                  WHERE sr2.site_id = sr.site_id
                    AND sr2.run_id < sr.run_id
                    AND r2.status = 'completed'
              )
        WHERE sr.run_id = ?
        ORDER BY sr.broken_count DESC, sr.site_name
        """,
        (run_id,)
    ).fetchall()
    result = []
    for s in sites:
        sd = dict(s)
        broken = conn.execute(
            "SELECT * FROM link_check_results "
            "WHERE run_id=? AND site_id=? ORDER BY source_page, link_url",
            (run_id, sd["site_id"])
        ).fetchall()
        sd["broken_links"] = [dict(b) for b in broken]
        result.append(sd)
    return result


# ─── Heartbeat Scans ─────────────────────────────────────────


def create_heartbeat_run() -> int:
    """Create a new heartbeat run record. Returns run_id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO heartbeat_runs (started_at) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    conn.commit()
    return cur.lastrowid


def finish_heartbeat_run(run_id: int, total: int, status: str = "completed"):
    """Mark a heartbeat run as finished."""
    conn = _get_conn()
    conn.execute(
        "UPDATE heartbeat_runs SET finished_at=?, status=?, total_sites=? WHERE id=?",
        (datetime.now().isoformat(), status, total, run_id)
    )
    conn.commit()


def save_heartbeat_result(run_id: int, result: dict):
    """Save a single site's heartbeat result."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO heartbeat_results "
        "(run_id, site_id, site_url, checked_at, dns_json, "
        " spf_status, spf_record, dkim_status, dkim_selector, "
        " dmarc_status, dmarc_record, smtp_status, smtp_detail, "
        " robots_status, robots_version, robots_content, "
        " sitemap_status, sitemap_url, staging_status, staging_final_url, "
        " staging_auth_status, wp_api_status, "
        " rdap_status, rdap_registrar, rdap_expires_at, rdap_created_at, "
        " rdap_status_flags, rdap_nameservers, rdap_json, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            result.get("site_id"),
            result.get("site_url", ""),
            datetime.now().isoformat(),
            result.get("dns_json"),
            result.get("spf_status"),
            result.get("spf_record"),
            result.get("dkim_status"),
            result.get("dkim_selector"),
            result.get("dmarc_status"),
            result.get("dmarc_record"),
            result.get("smtp_status"),
            result.get("smtp_detail"),
            result.get("robots_status"),
            result.get("robots_version"),
            result.get("robots_content"),
            result.get("sitemap_status"),
            result.get("sitemap_url"),
            result.get("staging_status"),
            result.get("staging_final_url"),
            result.get("staging_auth_status"),
            result.get("wp_api_status"),
            result.get("rdap_status"),
            result.get("rdap_registrar"),
            result.get("rdap_expires_at"),
            result.get("rdap_created_at"),
            result.get("rdap_status_flags"),
            result.get("rdap_nameservers"),
            result.get("rdap_json"),
            result.get("error"),
        )
    )
    conn.execute(
        "UPDATE heartbeat_runs SET checked = checked + 1 WHERE id = ?",
        (run_id,)
    )
    conn.commit()


def get_heartbeat_runs(limit: int = 50) -> list:
    """Return recent heartbeat runs, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM heartbeat_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_heartbeat_results(run_id: int) -> list:
    """Return all results for a heartbeat run."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM heartbeat_results WHERE run_id=? ORDER BY site_url",
        (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_heartbeat_for_site(site_id) -> dict | None:
    """Return the most recent heartbeat result for a site."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT hr.*, r.started_at AS run_started_at "
        "FROM heartbeat_results hr "
        "LEFT JOIN heartbeat_runs r ON r.id = hr.run_id "
        "WHERE hr.site_id = ? "
        "ORDER BY hr.id DESC LIMIT 1",
        (int(site_id),)
    ).fetchone()
    return dict(row) if row else None


def get_heartbeat_history_for_site(site_id) -> list:
    """Return all heartbeat results for a site, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT hr.*, r.started_at AS run_started_at "
        "FROM heartbeat_results hr "
        "LEFT JOIN heartbeat_runs r ON r.id = hr.run_id "
        "WHERE hr.site_id = ? "
        "ORDER BY hr.id DESC",
        (int(site_id),)
    ).fetchall()
    return [dict(r) for r in rows]
