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
    """)
    conn.commit()


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
            conn.execute(
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
            if conn.total_changes:
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
