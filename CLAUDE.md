# WSP Dashboard — Project Context for Claude

A local Python dashboard for managing 20–50 WordPress client sites via MainWP, Uptime Robot, and Cloudflare. Runs on `localhost:9111`. Single-user, passphrase-protected, all data stays local.

See `ROADMAP.md` for the phased development plan. See `WISHLIST.md` for parked feature ideas.

---

## Architecture

| File | Role |
|------|------|
| `server.py` | `http.server` + `ThreadingMixIn` entry point; thin dispatcher (`DashboardHandler` mixes in the `routes/*.py` modules) |
| `routes/` | Route handlers by feature (`sites.py`, `regression.py`, `linkcheck.py`, `onboarding.py`, `heartbeat.py`, `checklists.py`, `mainwp.py`, `cloudflare.py`, `uptime.py`, `auth.py`) plus shared state in `__init__.py` |
| `db.py` | SQLite persistence layer; WAL mode; thread-local connections |
| `config.py` | AES-256-GCM encrypted settings (passphrase-derived key) |
| `regression.py` | Playwright headless Chromium regression checker |
| `link_checker.py` | Broken-link checker |
| `heartbeat.py` | Site heartbeat scans |
| `checklist_csv.py` | Onboarding checklist template CSV import/export |
| `static/index.html` | HTML shell — markup only; no CSS or JS |
| `static/css/app.css` | All styling (single stylesheet) |
| `static/js/` | Vanilla JS frontend, split into ES modules — see **Frontend** below |
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
- No framework — vanilla JS only, split into native ES modules under `static/js/` (no build step; served directly, loaded via `<script type="module" src="/js/app.js">` in `index.html`)
- `appState` (in `state.js`) is the global state object — **not** `_appState`
- Each feature tab is its own module (`overview.js`, `regression.js`, `sites.js`, `onboarding.js`, `checklists.js`, etc.) and registers its own hash-route handler on import via `registerRoute(tab, fn)` in `router.js` — see `router.js`'s doc comment for why this registry pattern exists (it avoids import cycles between feature modules)
- Core/shared modules: `state.js` (appState/CONFIG), `api.js` (fetch wrapper), `dom.js` (escapeHtml, modal stack, shared helpers), `debug.js` (see below), `router.js` (hash routing + route registry), `logpanel.js` (on-screen log panel)
- `app.js` is the entry point — imports every module (most for their route-registration side effect only) and boots the app on `DOMContentLoaded`
- **Debug/wiring logging:** append `?debug` to the URL, or `localStorage.setItem('wsp_debug', '1')`, to enable verbose `[route]`/`[module]` console tracing (`debug.js`). Wiring failures (missing route handler, missing DOM element, uncaught error in a route handler) are logged via `dbgError` regardless of the debug flag, and surface in both the console and the on-screen log panel — check there first if a tab silently fails to load.
- `localStorage` keys: `wsp_log_panel` (log panel open/closed), `wsp_reg_selected` (regression site selection), `wsp_debug` (debug tracing), `wsp_last_subview` (per-tab last-used sub-view)
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
- **Downtime Log page removed (2026-07-08):** The standalone Downtime Log tab was dropped as part of the frontend module split — no plans to bring it back. Uptime Robot downtime data is still available via the API if needed later.
- **Size hotspots — grep before reading whole files:** `db.py` (~1450 lines) is the largest single file in the project. On the frontend, `linkcheck.js` and `regression.js` (~1250–1300 lines each) are the largest modules — still far smaller than the old 7,456-line monolith, but big enough that a targeted grep beats a full read.

---

## Dependencies

```bash
pip install -r requirements.txt   # requests, cryptography
pip install playwright Pillow      # regression + visual diff
playwright install chromium        # downloads ~150MB Chromium binary
```
