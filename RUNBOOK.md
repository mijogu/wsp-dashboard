# WSP Dashboard — Runbook

Commands, endpoints, and URLs referenced during development.

## Contents

- [Ubuntu Server (gunn-ubuntu-box)](#ubuntu-server-gunn-ubuntu-box)
- [Backups](#backups)
- [Running the Server](#running-the-server)
- [Setup (First Time)](#setup-first-time)
- [SQLite Database](#sqlite-database)
- [API Keys — Where to Find Them](#api-keys--where-to-find-them)
- [Dashboard API Endpoints](#dashboard-api-endpoints-localhost9111)
- [MainWP Discovery URLs](#mainwp-discovery-urls)
- [External API Endpoints](#external-api-endpoints-proxied-by-server)
- [Regression Testing (Layer 1)](#regression-testing-layer-1)
- [Visual Diff (Layer 2)](#visual-diff-layer-2)
- [Per-Site Configuration](#per-site-configuration)
- [Running Tests](#running-tests)
- [Files](#files)
- [Troubleshooting](#troubleshooting)

---

## Ubuntu Server (gunn-ubuntu-box)

Always-on machine used for persistent dashboard hosting, scheduled regression runs, and Mailpit email interception.

| Service         | URL                              | Notes                        |
|-----------------|----------------------------------|------------------------------|
| WSP Dashboard   | http://100.77.172.38:9111        | Accessible via Tailscale     |
| Uptime Kuma     | http://100.77.172.38:3001        | Already running              |
| Mailpit web UI  | http://100.77.172.38:8025        | Email catcher (see below)    |
| Mailpit SMTP    | 100.77.172.38:1025               | For WP test-mode SMTP config |
| SSH             | `ssh michael@100.77.172.38`      | Via Tailscale from any device|

### First-Time Setup on Ubuntu

```bash
# 1. Make 'python' and 'pip' work (Ubuntu defaults to python3 only)
sudo apt install -y python-is-python3 python3-pip

# 2. System libraries required by Playwright's headless Chromium
sudo apt install -y libglib2.0-0 libnss3 libnspr4 libatk1.0-0 \
  libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 libxcb1 \
  libxkbcommon0 libx11-6 libxcomposite1 libxdamage1 libxext6 \
  libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

# 3. Install Python dependencies
cd ~/wsp-dashboard
pip install -r requirements.txt

# 4. Install Playwright's Chromium binary (~150 MB)
playwright install chromium

# 5. First run — confirm it starts cleanly before setting up the service
python server.py
# Should log "Dashboard ready" and be reachable at http://100.77.172.38:9111
```

### systemd Service (Run on Boot)

Create the service file:

```bash
sudo nano /etc/systemd/system/wsp-dashboard.service
```

Paste:

```ini
[Unit]
Description=WSP Dashboard
After=network.target

[Service]
Type=simple
User=michael
WorkingDirectory=/home/michael/wsp-dashboard
ExecStart=/usr/bin/python /home/michael/wsp-dashboard/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable wsp-dashboard   # start on boot
sudo systemctl start wsp-dashboard    # start now
sudo systemctl status wsp-dashboard   # confirm running
```

Day-to-day service commands:

```bash
sudo systemctl restart wsp-dashboard   # after code updates
sudo systemctl stop wsp-dashboard      # stop it
sudo systemctl status wsp-dashboard    # check status
journalctl -u wsp-dashboard -f         # tail live logs
journalctl -u wsp-dashboard -n 100     # last 100 log lines
```

### Mailpit (Email Interception for Form Testing)

Mailpit catches all outbound email during regression test runs so nothing reaches real clients. Required for Layer 5 (form testing).

```bash
# Start Mailpit (Docker) — runs permanently, restarts on reboot
docker run -d --restart unless-stopped --name mailpit \
  -p 8025:8025 -p 1025:1025 axllent/mailpit

# Check it's running
docker ps | grep mailpit

# View logs
docker logs mailpit

# Stop / restart
docker stop mailpit
docker restart mailpit

# Update to latest version
docker pull axllent/mailpit
docker stop mailpit && docker rm mailpit
# then re-run the docker run command above
```

Mailpit web UI: **http://100.77.172.38:8025**
When the WSP Plugin test-mode is built, sites will be configured to use SMTP host `100.77.172.38` port `1025` during test runs.

### Deploying Code Updates

Ubuntu is configured as a git remote. A normal push deploys to both origin and Ubuntu simultaneously:

```bash
git push   # pushes to both remotes — Ubuntu receives the update automatically
```

**Auto-restart on push** — set up a git post-receive hook on Ubuntu so the server restarts automatically whenever new code arrives. SSH into Ubuntu and run:

```bash
# Allow passwordless systemctl restart for the hook
echo "michael ALL=(ALL) NOPASSWD: /bin/systemctl restart wsp-dashboard" \
  | sudo tee /etc/sudoers.d/wsp-dashboard

# Create the hook
cat > ~/wsp-dashboard/.git/hooks/post-receive << 'EOF'
#!/bin/bash
echo "--- Restarting wsp-dashboard service ---"
sudo systemctl restart wsp-dashboard
echo "--- Done ---"
EOF
chmod +x ~/wsp-dashboard/.git/hooks/post-receive
```

After this is set up, every `git push` from your Mac will deploy and restart the server in one step. You'll see the restart confirmation in your push output.

**Note:** `static/index.html` is served directly from disk — the server doesn't cache it, so frontend-only changes take effect immediately without a restart. Restarts are only strictly necessary when `.py` files change, but the hook restarts unconditionally for simplicity (it takes under a second).

Manual restart if needed:

```bash
ssh michael@100.77.172.38 "sudo systemctl restart wsp-dashboard"
```

### Database Note

`dashboard.db` on Ubuntu is separate from the copy on your Mac — configs, regression history, and site configs are **not** shared between machines. You'll need to re-enter your passphrase and API keys on first unlock via the Ubuntu URL.

---

## Backups

### WSP Dashboard Database

`dashboard.db` is a SQLite file. SQLite's `.backup` command produces a safe hot copy even while the server is running (safer than `cp` which can catch the file mid-write).

**Manual backup:**

```bash
# Safe hot backup (run on Ubuntu)
sqlite3 ~/wsp-dashboard/dashboard.db ".backup ~/wsp-dashboard/backups/dashboard-$(date +%F).db"

# Pull a copy to your Mac
scp michael@100.77.172.38:~/wsp-dashboard/dashboard.db ~/Desktop/dashboard-ubuntu-backup-$(date +%F).db
```

**Automated daily backup via cron** (set up once on Ubuntu):

```bash
# Create backup directory
mkdir -p ~/wsp-dashboard/backups

# Open crontab
crontab -e
```

Add this line (runs at 2 AM daily, keeps last 14 days):

```
0 2 * * * sqlite3 /home/michael/wsp-dashboard/dashboard.db ".backup /home/michael/wsp-dashboard/backups/dashboard-$(date +\%F).db" && find /home/michael/wsp-dashboard/backups -name "dashboard-*.db" -mtime +14 -delete
```

Verify cron is working after a day:

```bash
ls -lh ~/wsp-dashboard/backups/
```

### Uptime Kuma Database

Uptime Kuma stores its data in a Docker volume. Back it up by copying the SQLite file out of the container:

```bash
# Manual backup
docker cp uptime-kuma:/app/data/kuma.db ~/uptime-kuma-backup-$(date +%F).db

# Add to the same crontab line if you want it automated alongside the dashboard backup
```

To find where Docker has mounted the volume if the above path differs:

```bash
docker inspect uptime-kuma | grep -A5 Mounts
```

### Off-Site / Mac Backup

Pull both databases to your Mac in one command:

```bash
scp michael@100.77.172.38:~/wsp-dashboard/dashboard.db \
    michael@100.77.172.38:~/uptime-kuma-backup-*.db \
    ~/Desktop/ubuntu-backups/
```

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
- `regression_runs` — one row per regression check batch (start/end time, status, counts)
- `regression_results` — one row per site per run (HTTP status, JS errors, broken resources, screenshot path, diff score, diff path, load time)
- `baseline_screenshots` — stored baseline per `(site_id, page_url)` for visual diff
- `site_config` — per-site settings (client name, notes, test pages, diff threshold)

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
| `/api/regression/run`                 | POST   | Start a new regression check (returns immediately, runs in background) |
| `/api/regression/status`             | GET    | Playwright availability + active run progress |
| `/api/regression/latest`             | GET    | Most recent completed run + all results |
| `/api/regression/results/{run_id}`   | GET    | Results for a specific run |
| `/api/regression/runs`               | GET    | List of all runs (newest first) |
| `/api/regression/run/{run_id}`       | DELETE | Delete a completed run and its images (409 if run is active) |
| `/api/regression/cancel`             | POST   | Cancel the active run and clean up |
| `/api/regression/screenshot/{file}`  | GET    | Serve a captured screenshot or diff PNG |
| `/api/regression/baselines`          | GET    | All stored baselines (site_id + page_url → path) |
| `/api/regression/baseline/{result_id}` | POST | Set a result's screenshot as the baseline for that site+page |
| `/api/sites/config/{site_id}`        | GET    | Get per-site configuration |
| `/api/sites/config/{site_id}`        | POST   | Save per-site configuration |

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

## Regression Testing (Layer 1)

Layer 1 runs headless Chromium against each site's homepage and checks for:
HTTP status code, JavaScript console errors, broken resources (images/scripts/CSS returning 4xx/5xx), page load time, and captures a viewport screenshot.

### Setup (one-time)

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
pip install playwright
playwright install chromium
```

The `playwright install chromium` step downloads a Chromium binary (~150 MB) into `~/.cache/ms-playwright/`. No system browser is modified.

### Usage

Navigate to the **Regression** tab in the dashboard and click **Run Site Check**. The check runs in a background thread — a progress bar shows which site is currently being scanned. Results appear in the table once all sites are checked, with screenshots viewable in a modal.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/regression/run` | Start a new check (returns immediately) |
| GET | `/api/regression/status` | Playwright availability + active run progress |
| GET | `/api/regression/latest` | Most recent completed run + all results |
| GET | `/api/regression/results/{run_id}` | Results for a specific run |
| GET | `/api/regression/runs` | List of all runs (newest first) |
| GET | `/api/regression/screenshot/{filename}` | Serve a captured screenshot PNG |

### Files

| File | Purpose |
|------|---------|
| `regression.py` | Playwright-based site checker module |
| `screenshots/` | Auto-created directory for captured PNGs |
| DB: `regression_runs` | One row per check batch (start/end time, status, counts) |
| DB: `regression_results` | One row per site per run (HTTP, JS errors, broken, screenshot, load time) |

### Notes

- Only one run can be active at a time (second request returns 409)
- If Playwright is not installed, the tab shows install instructions and the Run button is disabled
- Screenshots are viewport-sized (1280×720), not full-page
- Configurable test pages per site via per-site config (see below); defaults to site homepage

---

## Visual Diff (Layer 2)

Visual diff automatically compares each new screenshot against a stored baseline to detect visual changes after updates. Produces a red-on-greyscale diff image and a 0–100% changed score.

### Setup (one-time)

```bash
pip install Pillow
```

The server logs a warning on startup if Pillow is not installed. Visual diff silently skips if Pillow is missing — check the Server Log panel if diff scores are not appearing.

### How it works

1. Run a regression check — screenshots are taken as usual
2. If no baseline exists for a site+page, the result shows "No baseline — set one to enable diff"
3. Click **Set as Baseline** on any result row to store that screenshot as the baseline
4. On the next run, the new screenshot is automatically diffed against the baseline
5. Diff score is shown in green / yellow / red relative to that site's configured threshold (default 1%)
6. Click the diff score to open a three-pane comparison modal: Baseline | Diff | Current

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/regression/baselines` | All stored baselines |
| POST | `/api/regression/baseline/{result_id}` | Promote a result's screenshot to baseline |

### Files

| Location | Purpose |
|----------|---------|
| `screenshots/` | All captured PNGs and diff images |
| `screenshots/diff_*.png` | Diff images (red-on-greyscale, prefixed `diff_`) |
| DB: `baseline_screenshots` | Stored baseline per `(site_id, page_url)` |
| DB: `regression_results.diff_score` | Computed diff % for each result |
| DB: `regression_results.diff_screenshot_path` | Path to the diff image |

---

## Per-Site Configuration

Stores site-specific settings that override global defaults.

### Configurable fields

| Field | Description |
|-------|-------------|
| Client name | Display name for the client (used for filtering) |
| Notes | Internal notes visible in the Sites panel |
| Test pages | List of URLs to check during regression (one per line); defaults to site homepage |
| Diff threshold | Max % change before a visual diff is flagged (default 1.0%) |

### Access

Navigate to the **Sites** tab in the dashboard. Click a site row to open the slide-out config panel.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sites/config/{site_id}` | Get config for a site |
| POST | `/api/sites/config/{site_id}` | Save config for a site |

---

## Running Tests

```bash
cd ~/DEV/wsp-dashboard
source venv/bin/activate
python -m unittest discover tests/ -v
```

All tests should pass in under a few seconds.
Each test gets an isolated temp database / config file; nothing touches `dashboard.db` or `config.enc`.

| Test file                          | What it covers                                      |
|------------------------------------|-----------------------------------------------------|
| `tests/test_config.py`             | AES-256-GCM encrypt/decrypt, save/load, export/import, session |
| `tests/test_db.py`                 | SQLite layer: save, dedup, filters, stats, site cache |
| `tests/test_regression_db.py`      | Regression DB layer: runs, results, baselines |
| `tests/test_regression_cancel.py`  | Cancel flag behavior |
| `tests/test_server_regression.py`  | HTTP regression endpoints |
| `tests/test_site_config.py`        | Per-site config CRUD, multi-page regression |

---

## Files

| File              | Purpose                                      |
|-------------------|----------------------------------------------|
| `server.py`       | Python HTTP server, API proxy, logging        |
| `config.py`       | AES-256-GCM encrypted config manager          |
| `db.py`           | SQLite persistence layer                      |
| `regression.py`   | Playwright-based regression checker (Layer 1 + Layer 2) |
| `static/index.html` | Dashboard frontend (single-file)           |
| `requirements.txt`| Python dependencies                           |
| `config.enc`      | Encrypted API keys (gitignored)               |
| `.session`        | Saved session for auto-unlock (gitignored)    |
| `dashboard.db`    | SQLite database (gitignored)                  |
| `screenshots/`    | Captured PNGs and diff images (auto-created)  |
| `CLAUDE.md`       | Project context for Claude Code sessions      |
| `ROADMAP.md`      | Phased development plan                       |
| `WISHLIST.md`     | Parked feature ideas for later                |
| `tests/`          | Unit test suite (6 files)                     |

---

## Troubleshooting

- **"Failed to load dashboard data"** — One or more APIs returned an error. Check the Server Log panel at the bottom of the dashboard.
- **MainWP returns no data** — Verify the API key has Read permissions. Check the server log for HTTP status codes and raw response previews.
- **CORS errors** — Shouldn't happen with the Python proxy. If you see them, you're probably opening index.html directly instead of going through localhost:9111.
- **Passphrase not working** — Delete `config.enc` and `.session` from the project directory and restart. You'll set a new passphrase.
- **Port in use** — Change `PORT = 9111` in server.py.
- **Visual diff scores not appearing** — Pillow is not installed. Run `pip install Pillow` and restart the server. The startup log will say "Pillow not installed — visual diff disabled" if this is the issue. After installing, check the Server Log for messages like `"Visual diff example.com: 2.34% changed"` to confirm it's working.
- **Visual diff running but score always 0%** — URL key mismatch between the stored baseline and the current result. Server log will show the baseline keys it found vs. what it's looking for. Usually caused by a trailing-slash difference; the code normalizes these, but check the log for confirmation.
- **Regression run won't start (409)** — A run is already active. Check `/api/regression/status` or wait for it to finish. If the server was restarted mid-run, the active run state is lost — restart will clear it.
