# WSP Dashboard — Runbook

Commands, endpoints, and URLs referenced during development.

---

## Running the Server

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
python3 server.py
# Dashboard at: http://localhost:9111
```

## Setup (First Time)

```bash
cd ~/DEV/wsp-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

---

## SQLite Database

**File:** `dashboard.db` (created automatically next to `server.py`)

**Tables:**
- `update_history` — every plugin/theme/WP update fetched from Pro Reports
- `fetch_log` — metadata for each fetch run (when, how many records, how many new)
- `sites_cache` — snapshot of MainWP site info (refreshed on each sites fetch)

**Inspect directly:**
```bash
cd ~/DEV/wsp-dashboard
sqlite3 dashboard.db
.tables
SELECT COUNT(*), MIN(updated_utime), MAX(updated_utime) FROM update_history;
SELECT site_name, COUNT(*) AS updates FROM update_history GROUP BY site_name ORDER BY updates DESC;
.quit
```

---

## API Keys — Where to Find Them

| Service       | Where                                                          | Notes                              |
|---------------|----------------------------------------------------------------|------------------------------------|
| Uptime Robot  | My Settings > API Settings                                     | Use main or read-only key          |
| Cloudflare    | My Profile > API Tokens                                        | Needs Zone:Read, Analytics:Read    |
| Cloudflare ID | Any zone's Overview page, right sidebar                        | 32-char hex, optional              |
| MainWP        | API Access > API Keys > Add API Keys                           | Bearer token, shown once — copy it |

---

## Dashboard API Endpoints (localhost:9111)

| Endpoint                              | Method | Description                        |
|---------------------------------------|--------|------------------------------------|
| `/api/status`                         | GET    | Check unlock state and config      |
| `/api/unlock`                         | POST   | Unlock with passphrase             |
| `/api/settings`                       | GET    | Get current settings               |
| `/api/settings`                       | POST   | Save settings (encrypted to disk)  |
| `/api/uptime-robot`                   | GET    | Proxy: Uptime Robot monitors       |
| `/api/cloudflare/zones`               | GET    | Proxy: Cloudflare zones            |
| `/api/cloudflare/analytics/{zoneId}`  | GET    | Proxy: Cloudflare zone analytics   |
| `/api/mainwp/sites`                   | GET    | Proxy: MainWP child sites          |
| `/api/mainwp/updates`                 | GET    | Proxy: MainWP pending updates      |
| `/api/mainwp/raw/{path}`              | GET    | Proxy: any MainWP v2 endpoint      |
| `/api/mainwp/routes`                  | GET    | Discover all MainWP REST routes    |
| `/api/mainwp/update-history`          | GET    | Fetch live update history from Pro Reports (saves to DB). Default: incremental from last fetch. Params: `start_date=YYYY-MM-DD`, `end_date=YYYY-MM-DD` |
| `/api/mainwp/update-history/cached`   | GET    | Load stored update history from SQLite (no API call) |
| `/api/mainwp/update-history/cached?format=csv` | GET | Export full cached history as CSV |
| `/api/db/stats`                       | GET    | SQLite stats: record count, date range, last fetch, `last_fetch_date` for incremental sync |
| `/api/logs`                           | GET    | Server log entries                 |
| `/api/export`                         | GET    | Export encrypted config (base64)   |
| `/api/import`                         | POST   | Import encrypted config            |

---

## MainWP Discovery URLs

Use these in your browser to explore what MainWP returns:

```
http://localhost:9111/api/mainwp/raw/sites
http://localhost:9111/api/mainwp/raw/sites/69          # Single site (Aprea, id=69)
http://localhost:9111/api/mainwp/raw/updates
http://localhost:9111/api/mainwp/raw/updates?type=plugins
http://localhost:9111/api/mainwp/raw/updates?type=themes
http://localhost:9111/api/mainwp/raw/updates?type=wp
http://localhost:9111/api/mainwp/raw/reports            # Pro Reports (if available)
http://localhost:9111/api/mainwp/raw/clients
http://localhost:9111/api/mainwp/routes                 # All registered MainWP REST routes
```

---

## External API Endpoints (proxied by server)

### Uptime Robot v2
- `POST https://api.uptimerobot.com/v2/getMonitors`
- Auth: `api_key` in JSON body

### Cloudflare v4
- `GET https://api.cloudflare.com/client/v4/zones`
- `GET https://api.cloudflare.com/client/v4/zones/{id}/analytics/dashboard`
- Auth: `Authorization: Bearer {token}`

### MainWP v2
- `GET {dashboard}/wp-json/mainwp/v2/sites`
- `GET {dashboard}/wp-json/mainwp/v2/sites/basic`
- `GET {dashboard}/wp-json/mainwp/v2/sites/{id}`
- `GET {dashboard}/wp-json/mainwp/v2/updates`
- `GET {dashboard}/wp-json/mainwp/v2/updates?type=plugins`
- Auth: `Authorization: Bearer {token}`

---

## Running Tests

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
python -m unittest discover tests/ -v
```

**53 tests across 2 files** — all should pass in under 2 seconds.
Each test gets an isolated temp database / config file; nothing touches `dashboard.db` or `config.enc`.

| Test file               | What it covers                                      |
|-------------------------|-----------------------------------------------------|
| `tests/test_db.py`      | SQLite layer: save, dedup, filters, stats, site cache |
| `tests/test_config.py`  | AES-256-GCM encrypt/decrypt, save/load, export/import, session |

---

## Files

| File              | Purpose                                      |
|-------------------|----------------------------------------------|
| `server.py`       | Python HTTP server, API proxy, logging        |
| `config.py`       | AES-256-GCM encrypted config manager          |
| `db.py`           | SQLite persistence layer                      |
| `config.enc`      | Encrypted API keys (gitignored)               |
| `.session`        | Saved session for auto-unlock (gitignored)    |
| `dashboard.db`    | SQLite database — update history, site cache (gitignored) |
| `static/index.html` | Dashboard frontend (single-file)           |
| `requirements.txt`| Python dependencies                           |
| `tests/test_db.py`    | Unit tests for db.py                      |
| `tests/test_config.py` | Unit tests for config.py                 |

---

## Troubleshooting

- **"Failed to load dashboard data"** — One or more APIs returned an error. Check the Server Log panel at the bottom of the dashboard.
- **MainWP returns no data** — Verify the API key has Read permissions. Check the server log for HTTP status codes and raw response previews.
- **CORS errors** — Shouldn't happen with the Python proxy. If you see them, you're probably opening index.html directly instead of going through localhost:9111.
- **Passphrase not working** — Delete `config.enc` and `.session` from the project directory and restart. You'll set a new passphrase.
- **Port in use** — Change `PORT = 9111` in server.py.
