# WSP Dashboard — Project Context for Claude

A local Python dashboard for managing 20–50 WordPress client sites via MainWP, Uptime Robot, and Cloudflare. Runs on `localhost:9111`. Single-user, passphrase-protected, all data stays local.

See `ROADMAP.md` for the phased development plan. See `WISHLIST.md` for parked feature ideas.

---

## Architecture

| File | Role |
|------|------|
| `server.py` | `http.server` + `ThreadingMixIn` entry point; `DashboardHandler` handles all routes |
| `db.py` | SQLite persistence layer; WAL mode; thread-local connections |
| `config.py` | AES-256-GCM encrypted settings (passphrase-derived key) |
| `regression.py` | Playwright headless Chromium regression checker |
| `static/index.html` | Single-file vanilla JS frontend |
| `tests/` | Unit tests — run with `python -m unittest discover tests/ -v` |
| `screenshots/` | Captured PNGs and diff images from regression runs |
| `dashboard.db` | SQLite database (gitignored) |
| `config.enc` | Encrypted API keys (gitignored) |

---

## Key Conventions

### SQLite / db.py
- **Always use `_get_conn()`** — never create `sqlite3.Connection` directly. It returns a thread-local connection.
- **Schema migrations:** `ALTER TABLE ... ADD COLUMN` wrapped in `try/except` (idempotent). New tables go in the `executescript` block inside `init_db()`.
- All schema changes go through `init_db()` migrations list — never run DDL elsewhere.

### Thread Safety
- `_lock` in `server.py` — protects `_settings` and `_passphrase` globals
- `_run_lock` in `regression.py` — protects `_active_run` and `_cancel_requested` globals
- Thread-local DB connections (via `threading.local()`) — safe to use from any thread without locking

### Regression
- Only one run can be active at a time — second request returns 409
- `_cancel_requested` flag is checked between every page; checked in `run_checks()` loop
- `delete_regression_run()` returns both screenshot paths AND diff image paths — callers must handle both for cleanup
- Baselines are loaded at run start and passed to `run_checks()` as a dict

### Frontend
- No framework — vanilla JS only
- `_appState` is the global state object
- `localStorage` keys: `wsp_log_panel` (log panel open/closed), `wsp_reg_selected` (regression site selection)
- All site IDs are normalized to strings (`String(s.id)`) throughout the UI

### Config / Security
- Never log or expose the passphrase
- Settings are encrypted on every save via `config.py`
- Session file (`.session`) is base64-obfuscated passphrase on disk — local machine only, acceptable risk

---

## Running the Server

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
python3 server.py
# Dashboard at: http://localhost:9111
```

## Running Tests

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
python -m unittest discover tests/ -v
```

| Test file | What it covers |
|-----------|---------------|
| `tests/test_config.py` | AES-256-GCM encrypt/decrypt, save/load, export/import, session |
| `tests/test_db.py` | SQLite layer: save, dedup, filters, stats, site cache |
| `tests/test_regression_db.py` | Regression DB layer: runs, results, baselines |
| `tests/test_regression_cancel.py` | Cancel flag behavior |
| `tests/test_server_regression.py` | HTTP regression endpoints |
| `tests/test_site_config.py` | Site config CRUD, multi-page regression |

Tests use isolated temp DBs and config files — nothing touches `dashboard.db` or `config.enc`.

---

## Known Issues / Gotchas

- **Pillow not installed:** Visual diff (Layer 2) requires `pip install Pillow`. Server logs a warning on startup if missing. If diff scores aren't appearing, this is the first thing to check.
- **DB corruption (2026-03-26):** `dashboard.db` was corrupted (zero-byte header). Restored from backup. Corrupted file preserved as `dashboard-corrupted-2026-03-26.db`. Current backups: `dashboard-3-19-2026-BAK.db`, `dashboard-3-26-2026-BAK.db`.
- **server.py size:** Currently ~52KB in a single class — Phase 0 refactor splits it into route modules.

---

## Dependencies

```bash
pip install -r requirements.txt   # requests, cryptography
pip install playwright Pillow      # regression + visual diff
playwright install chromium        # downloads ~150MB Chromium binary
```
