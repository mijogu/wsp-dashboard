"""
Tests for db.py — SQLite persistence layer.

Run with:  python -m pytest tests/   (if pytest available)
       or:  python -m unittest discover tests/
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db


def make_record(site_id="1", site_name="Test Site", update_type="plugins",
                name="my-plugin", utime="2026-03-01 12:00:00",
                old_version="1.0", new_version="1.1"):
    return {
        "_site_id":       site_id,
        "_site_name":     site_name,
        "_site_url":      "https://test.example.com",
        "_update_type":   update_type,
        "name":           name,
        "old_version":    old_version,
        "current_version": new_version,
        "updated_date":   utime[:10],
        "updated_time":   utime[11:],
        "updated_utime":  utime,
        "updated_author": "admin",
        "updated_slug":   name,
    }


class DBTestBase(unittest.TestCase):
    """Each test gets an isolated temp DB."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        self._patcher = patch.object(db, "DB_PATH", self._db_path)
        self._patcher.start()
        db._local.conn = None   # reset thread-local so _get_conn opens fresh
        db.init_db()

    def tearDown(self):
        conn = getattr(db._local, "conn", None)
        if conn:
            conn.close()
            db._local.conn = None
        self._patcher.stop()


# ─── Empty-state tests ────────────────────────────────────────────────────────

class TestEmptyDB(DBTestBase):

    def test_get_update_history_empty(self):
        self.assertEqual(db.get_update_history(), [])

    def test_get_history_stats_zeros(self):
        s = db.get_history_stats()
        self.assertEqual(s["total_records"], 0)
        self.assertEqual(s["unique_sites"], 0)
        self.assertIsNone(s["oldest_record"])
        self.assertIsNone(s["newest_record"])
        self.assertIsNone(s["last_fetch"])

    def test_get_last_fetch_date_none(self):
        self.assertIsNone(db.get_last_fetch_date())

    def test_get_cached_sites_empty(self):
        self.assertEqual(db.get_cached_sites(), [])

    def test_get_cache_age_none(self):
        self.assertIsNone(db.get_cache_age())


# ─── save_update_records ──────────────────────────────────────────────────────

class TestSaveUpdateRecords(DBTestBase):

    def test_basic_save_returns_counts(self):
        result = db.save_update_records([make_record()], "2026-03-01", "2026-03-02", 1, 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["duplicate"], 0)
        self.assertIsNotNone(result["fetch_id"])

    def test_saved_record_is_retrievable(self):
        rec = make_record(name="akismet", site_name="My Blog")
        db.save_update_records([rec], "2026-03-01", "2026-03-02", 1, 1)
        rows = db.get_update_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "akismet")
        self.assertEqual(rows[0]["site_name"], "My Blog")

    def test_duplicate_not_inserted_twice(self):
        rec = make_record()
        db.save_update_records([rec], "2026-03-01", "2026-03-02", 1, 1)
        r2 = db.save_update_records([rec], "2026-03-01", "2026-03-02", 1, 1)
        self.assertEqual(r2["new"], 0)
        self.assertEqual(r2["duplicate"], 1)
        self.assertEqual(len(db.get_update_history()), 1)

    def test_dedup_counts_accurate_across_multiple_records(self):
        """Regression test for the total_changes bug — counts must be per-row."""
        rec_a = make_record(name="plugin-a", utime="2026-03-01 10:00:00")
        rec_b = make_record(name="plugin-b", utime="2026-03-01 11:00:00")
        rec_c = make_record(name="plugin-c", utime="2026-03-01 12:00:00")

        r1 = db.save_update_records([rec_a, rec_b, rec_c],
                                    "2026-03-01", "2026-03-02", 1, 1)
        self.assertEqual(r1["new"], 3)
        self.assertEqual(r1["duplicate"], 0)

        # Second batch: rec_a is a dup, rec_d is new
        rec_d = make_record(name="plugin-d", utime="2026-03-01 13:00:00")
        r2 = db.save_update_records([rec_a, rec_d],
                                    "2026-03-01", "2026-03-02", 1, 1)
        self.assertEqual(r2["new"], 1)
        self.assertEqual(r2["duplicate"], 1)

    def test_saves_multiple_records(self):
        records = [
            make_record(name="plugin-a", utime="2026-03-01 10:00:00"),
            make_record(name="plugin-b", utime="2026-03-01 11:00:00"),
            make_record(name="plugin-c", utime="2026-03-01 12:00:00"),
        ]
        result = db.save_update_records(records, "2026-03-01", "2026-03-02", 1, 1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["new"], 3)
        self.assertEqual(len(db.get_update_history()), 3)

    def test_creates_fetch_log_entry(self):
        result = db.save_update_records([make_record()], "2026-03-01", "2026-03-07", 6, 5)
        conn = db._get_conn()
        row = conn.execute(
            "SELECT * FROM fetch_log WHERE id = ?", (result["fetch_id"],)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["date_from"], "2026-03-01")
        self.assertEqual(row["date_to"], "2026-03-07")
        self.assertEqual(row["days"], 6)
        self.assertEqual(row["sites_queried"], 5)
        self.assertEqual(row["records_found"], 1)
        self.assertEqual(row["records_new"], 1)

    def test_same_plugin_on_two_sites_is_two_rows(self):
        rec1 = make_record(site_id="1", site_name="Site A", name="woo")
        rec2 = make_record(site_id="2", site_name="Site B", name="woo")
        result = db.save_update_records([rec1, rec2], "2026-03-01", "2026-03-02", 1, 2)
        self.assertEqual(result["new"], 2)
        self.assertEqual(len(db.get_update_history()), 2)


# ─── get_update_history filters ──────────────────────────────────────────────

class TestGetUpdateHistoryFilters(DBTestBase):

    def setUp(self):
        super().setUp()
        records = [
            make_record(site_id="1", site_name="Alpha", update_type="plugins",
                        name="plugin-a", utime="2026-01-15 10:00:00"),
            make_record(site_id="1", site_name="Alpha", update_type="themes",
                        name="theme-x", utime="2026-02-10 10:00:00"),
            make_record(site_id="2", site_name="Beta",  update_type="plugins",
                        name="plugin-b", utime="2026-03-01 10:00:00"),
            make_record(site_id="2", site_name="Beta",  update_type="wordpress",
                        name="WordPress", utime="2026-03-05 10:00:00"),
        ]
        db.save_update_records(records, "2026-01-01", "2026-03-31", 90, 2)

    def test_no_filters_returns_all(self):
        self.assertEqual(len(db.get_update_history()), 4)

    def test_filter_by_plugins(self):
        rows = db.get_update_history(update_type="plugins")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["update_type"] == "plugins" for r in rows))

    def test_filter_by_themes(self):
        rows = db.get_update_history(update_type="themes")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "theme-x")

    def test_filter_by_site_id(self):
        rows = db.get_update_history(site_id="2")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["site_name"] == "Beta" for r in rows))

    def test_combined_type_and_site(self):
        rows = db.get_update_history(update_type="plugins", site_id="2")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "plugin-b")

    def test_results_ordered_newest_first(self):
        rows = db.get_update_history()
        utimes = [r["updated_utime"] for r in rows]
        self.assertEqual(utimes, sorted(utimes, reverse=True))

    def test_large_days_returns_all(self):
        self.assertEqual(len(db.get_update_history(days=9999)), 4)


# ─── get_history_stats ────────────────────────────────────────────────────────

class TestGetHistoryStats(DBTestBase):

    def test_stats_after_save(self):
        records = [
            make_record(site_id="1", update_type="plugins",
                        name="p1", utime="2026-02-01 10:00:00"),
            make_record(site_id="2", update_type="themes",
                        name="t1", utime="2026-03-01 10:00:00"),
        ]
        db.save_update_records(records, "2026-02-01", "2026-03-01", 28, 2)
        s = db.get_history_stats()
        self.assertEqual(s["total_records"], 2)
        self.assertEqual(s["unique_sites"], 2)
        self.assertEqual(s["oldest_record"], "2026-02-01 10:00:00")
        self.assertEqual(s["newest_record"], "2026-03-01 10:00:00")
        self.assertIsNotNone(s["last_fetch"])
        self.assertEqual(s["last_fetch"]["records_found"], 2)


# ─── get_last_fetch_date ──────────────────────────────────────────────────────

class TestGetLastFetchDate(DBTestBase):

    def test_none_when_no_fetches(self):
        self.assertIsNone(db.get_last_fetch_date())

    def test_returns_date_to_of_most_recent_fetch(self):
        db.save_update_records([make_record()], "2026-01-01", "2026-01-31", 30, 1)
        db.save_update_records(
            [make_record(name="p2", utime="2026-03-01 10:00:00")],
            "2026-02-01", "2026-03-15", 42, 1
        )
        self.assertEqual(db.get_last_fetch_date(), "2026-03-15")


# ─── Sites cache ──────────────────────────────────────────────────────────────

class TestSitesCache(DBTestBase):

    SITES = [
        {"id": 1, "name": "Alpha Site", "url": "https://alpha.example.com", "extra": "d1"},
        {"id": 2, "name": "Beta Site",  "url": "https://beta.example.com",  "extra": "d2"},
    ]

    def test_roundtrip(self):
        db.cache_sites(self.SITES)
        cached = db.get_cached_sites()
        self.assertEqual(len(cached), 2)
        names = {s["name"] for s in cached}
        self.assertEqual(names, {"Alpha Site", "Beta Site"})

    def test_full_data_preserved(self):
        db.cache_sites(self.SITES)
        by_name = {s["name"]: s for s in db.get_cached_sites()}
        self.assertEqual(by_name["Alpha Site"]["extra"], "d1")
        self.assertEqual(by_name["Alpha Site"]["url"], "https://alpha.example.com")

    def test_replace_on_conflict(self):
        db.cache_sites([{"id": 1, "name": "Old Name", "url": ""}])
        db.cache_sites([{"id": 1, "name": "New Name", "url": ""}])
        cached = db.get_cached_sites()
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["name"], "New Name")

    def test_cache_age_returns_timestamp(self):
        db.cache_sites(self.SITES)
        age = db.get_cache_age()
        self.assertIsNotNone(age)
        self.assertGreaterEqual(len(age), 10)

    def test_sites_without_id_skipped(self):
        db.cache_sites([{"name": "No ID", "url": "https://x.com"}])
        self.assertEqual(db.get_cached_sites(), [])

    def test_ordered_by_name(self):
        db.cache_sites([
            {"id": 3, "name": "Zebra", "url": ""},
            {"id": 1, "name": "Apple", "url": ""},
            {"id": 2, "name": "Mango", "url": ""},
        ])
        names = [s["name"] for s in db.get_cached_sites()]
        self.assertEqual(names, ["Apple", "Mango", "Zebra"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
