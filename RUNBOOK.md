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
| `/api/mainwp/update-history`          | GET    | Pro Reports update history (JSON)  |
| `/api/mainwp/update-history?format=csv` | GET  | Pro Reports update history (CSV)   |
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

## Files

| File              | Purpose                                      |
|-------------------|----------------------------------------------|
| `server.py`       | Python HTTP server, API proxy, logging        |
| `config.py`       | AES-256-GCM encrypted config manager          |
| `config.enc`      | Encrypted API keys (gitignored)               |
| `.session`        | Saved session for auto-unlock (gitignored)    |
| `static/index.html` | Dashboard frontend (single-file)           |
| `requirements.txt`| Python dependencies                           |

---

## Troubleshooting

- **"Failed to load dashboard data"** — One or more APIs returned an error. Check the Server Log panel at the bottom of the dashboard.
- **MainWP returns no data** — Verify the API key has Read permissions. Check the server log for HTTP status codes and raw response previews.
- **CORS errors** — Shouldn't happen with the Python proxy. If you see them, you're probably opening index.html directly instead of going through localhost:9111.
- **Passphrase not working** — Delete `config.enc` and `.session` from the project directory and restart. You'll set a new passphrase.
- **Port in use** — Change `PORT = 9111` in server.py.
