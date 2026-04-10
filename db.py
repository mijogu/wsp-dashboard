"""
WP Maintenance Dashboard — SQLite persistence layer.

Stores:
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

        -- Visual regression baselines: one screenshot per (site_id, page_url)
        CREATE TABLE IF NOT EXISTS baseline_screenshots (
            site_id         TEXT NOT NULL,
            page_url        TEXT NOT NULL,
            screenshot_path TEXT NOT NULL,
            run_id          INTEGER NOT NULL,
            set_at          TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (site_id, page_url)
        );
    """)
    conn.commit()

    # Migrations: add columns that may not exist in older DBs
    for migration in [
        "ALTER TABLE regression_results ADD COLUMN page_url TEXT",
        "ALTER TABLE regression_results ADD COLUMN diff_score REAL",
        "ALTER TABLE regression_results ADD COLUMN diff_screenshot_path TEXT",
        "ALTER TABLE site_config ADD COLUMN diff_threshold REAL DEFAULT 1.0",
        "ALTER TABLE regression_results ADD COLUMN prev_screenshot_path TEXT",
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
            "test_pages": "[]", "diff_threshold": 1.0, "updated_at": None}


def save_site_config(site_id, client_name: str = None, notes: str = None,
                     test_pages: str = "[]", diff_threshold: float = None) -> None:
    """Insert or replace config for a site."""
    conn = _get_conn()
    threshold = diff_threshold if diff_threshold is not None else 1.0
    conn.execute(
        "INSERT INTO site_config (site_id, client_name, notes, test_pages, diff_threshold, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(site_id) DO UPDATE SET "
        "  client_name = excluded.client_name, "
        "  notes = excluded.notes, "
        "  test_pages = excluded.test_pages, "
        "  diff_threshold = excluded.diff_threshold, "
        "  updated_at = excluded.updated_at",
        (str(site_id), client_name or None, notes or None, test_pages or "[]", threshold)
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
