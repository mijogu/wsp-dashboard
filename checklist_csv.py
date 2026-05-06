"""
checklist_csv.py — CSV-backed onboarding checklist template loader.

The file at DATA_PATH is the single source of truth for step definitions.
It is read fresh on every call (so edits take effect on next page load).
If the file is missing on server startup, bootstrap_if_missing() writes it
from the current onboarding_fields rows in the DB.
"""

import csv
import io
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "onboarding_checklist.csv")

COLUMNS = [
    "id",
    "section",
    "name",
    "description",
    "field_type",
    "options",
    "requires_new_client",
    "requires_current_client",
    "requires_redesign",
    "requires_new_site",
    "requires_custom_host",
    "target_field_id",
]

_BOOL_COLS = {
    "requires_new_client",
    "requires_current_client",
    "requires_redesign",
    "requires_new_site",
    "requires_custom_host",
}


def load_checklist_template():
    """
    Parse the CSV and return:
      {
        "sections": ["Infrastructure", "Staging", ...],   # ordered, deduplicated
        "steps": [
          {
            "id": "hosting_provider",
            "section": "Infrastructure",
            "name": "Hosting Provider",
            "description": "",
            "field_type": "text",
            "options": [],                  # list (split on '|')
            "requires_new_client": False,
            "requires_current_client": False,
            "requires_redesign": False,
            "requires_new_site": False,
            "requires_custom_host": False,
            "target_field_id": "hosting_provider",
          },
          ...
        ]
      }
    Row order is CSV row order (no position column).
    """
    steps = []
    sections_seen = []

    with open(DATA_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            step = {}
            for col in COLUMNS:
                val = row.get(col, "").strip()
                if col in _BOOL_COLS:
                    step[col] = val == "1"
                elif col == "options":
                    step[col] = [o.strip() for o in val.split("|") if o.strip()] if val else []
                else:
                    step[col] = val
            if not step.get("id"):
                continue  # skip blank rows
            steps.append(step)
            sec = step["section"]
            if sec and sec not in sections_seen:
                sections_seen.append(sec)

    return {"sections": sections_seen, "steps": steps}


def bootstrap_if_missing():
    """
    If DATA_PATH does not exist, write it from the current onboarding_fields
    rows in the database.  Called once at server startup.
    """
    if os.path.exists(DATA_PATH):
        return

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    # Import here to avoid circular imports at module load time
    import db as _db
    conn = _db._get_conn()
    rows = conn.execute(
        "SELECT id, name, group_name, field_type, options, position "
        "FROM onboarding_fields ORDER BY group_name, position"
    ).fetchall()

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            # options is stored as JSON array "[]" or '["a","b"]' — normalise to pipe-sep
            import json as _json
            try:
                opts = _json.loads(r["options"] or "[]")
                opts_str = "|".join(opts) if opts else ""
            except Exception:
                opts_str = r["options"] or ""

            writer.writerow({
                "id": r["id"],
                "section": r["group_name"],
                "name": r["name"],
                "description": "",
                "field_type": r["field_type"],
                "options": opts_str,
                "requires_new_client": "",
                "requires_current_client": "",
                "requires_redesign": "",
                "requires_new_site": "",
                "requires_custom_host": "",
                "target_field_id": r["id"],
            })
