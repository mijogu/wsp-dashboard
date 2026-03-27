# WSP Dashboard — Development Roadmap

A phased plan for developing the WSP Dashboard beyond its current state. Each phase has a goal, concrete tasks, done criteria, and a complexity estimate (S/M/L/XL).

The **WISHLIST.md** file is the parking lot for ideas. This ROADMAP is the prioritized execution plan.

---

## Current State (as of 2026-03-27)

**Shipped:**
- Core proxy infrastructure (MainWP, Uptime Robot, Cloudflare)
- SQLite persistence for update history and site cache
- Incremental sync from Pro Reports API
- Regression Layer 1: HTTP checks + screenshots (Playwright)
- Regression Layer 2: Visual diff (Pillow pixel diff, baseline management)
- Per-site configuration (diff threshold, test pages, notes)
- Cancel + delete regression runs

**Pending UX fix (carry into Phase 0):** Replace per-run dropdown with a site-centric persistent table as the default regression view.

---

## Phase 0 — Documentation, Refactor & UX Cleanup

**Goal:** Get the codebase and docs in order before adding any new features.

**Complexity:** M

**Tasks:**

*Documentation:*
- [ ] Create `CLAUDE.md` — project context for Claude Code sessions (conventions, architecture, test patterns)
- [ ] Update `RUNBOOK.md` — Layer 2 visual diff, per-site config, new API endpoints, correct test count (6 files, not 2)
- [ ] Add Pillow troubleshooting note to RUNBOOK (visual diff not showing → `pip install Pillow`)

*Refactor:*
- [ ] Split `server.py` (currently 52KB / 1000+ lines) into route modules
  - `server.py` becomes a thin entry point and router
  - Each logical area (settings, mainwp, regression, cloudflare, uptime) lives in its own module
  - Keep `http.server` + `ThreadingMixIn` — no framework migration needed
  - All existing tests must pass after refactor

*UX fix:*
- [ ] Replace per-run dropdown with a site-centric persistent table as the default regression view
  - Persistent table: all sites + their latest result + diff score
  - Run dropdown becomes a secondary "historical view" mode

**Done criteria:** CLAUDE.md and updated RUNBOOK exist; `server.py` is split into modules with all tests passing; regression default view is site-centric.

**Dependencies:** None — do this first.

---

## Phase 1 — Layer 3: Broken Link Checker

**Goal:** Detect broken links (4xx/5xx) across each site's pages as part of the regression run.

**Complexity:** M

**Tasks:**
- [ ] After Playwright screenshot, extract all `<a href>` links on the page
- [ ] Issue HEAD (fallback GET) requests in parallel; log status codes
- [ ] New `broken_links` DB table: `(result_id, url, status_code, checked_at)`
- [ ] UI: "Broken Links" count column in results table
- [ ] UI: click count to expand per-site link list with status codes
- [ ] Scope decision at build time: homepage-only links vs. depth-2 crawl (start with homepage-only)

**Done criteria:** Regression run reports broken link count per site; results stored in DB; UI shows counts with drill-down detail.

**Dependencies:** Phase 0 (server refactor makes integration cleaner).

---

## Phase 2 — Client / Tag Management

**Goal:** Tag sites by client so all dashboard views can be filtered to a single client.

**Complexity:** M

**Tasks:**
- [ ] Add `client_name` and `client_slug` fields to `site_config` table (already exists — low-lift migration)
- [ ] UI: client selector dropdown in header that applies a global filter to all tabs
- [ ] Filtered views in: Updates tab, Regression tab, Uptime tab
- [ ] Optional: promote to a full `clients` table if client metadata grows beyond name/slug

**Done criteria:** Sites can be tagged to a client; all tabs filter when a client is selected.

**Dependencies:** None technically, but cleaner after Phase 0 refactor.

**Note:** This is a prerequisite for Phase 6 (client-facing views).

---

## Phase 3 — Layer 4: URL Snapshot + Change Tracking

**Goal:** Export all URLs from each WP site, store snapshots over time, and report on URLs that were added, removed, or changed between snapshots.

**Complexity:** M

**Tasks:**
- [ ] Parse `/wp-sitemap.xml` per site (WP 5.5+, no auth needed); fallback to `/sitemap.xml`
- [ ] New `url_snapshots` table: `(site_id, snapshot_date, url_count, urls JSON)`
- [ ] Diff consecutive snapshots: added / removed / changed (status code changed, redirect added)
- [ ] UI: "URL Changes" section in Regression tab
- [ ] UI: snapshot history per site with diff view
- [ ] CSV export of URL changes

**Done criteria:** Snapshots can be taken manually; diff between any two snapshots is shown in UI; CSV export works.

**Dependencies:** Phase 0.

---

## Phase 4 — Alerting & Notifications

**Goal:** Notify when regression runs find issues or when sites have stale pending updates.

**Complexity:** S

**Tasks:**
- [ ] Email or webhook alert (POST to configurable URL) when a regression run completes with failures (broken links, visual diff above threshold, HTTP errors)
- [ ] Configurable webhook URL stored in encrypted settings
- [ ] UI: staleness warning in Update History tab when a site has pending updates older than N days (N configurable per site or globally)

**Done criteria:** Webhook fires after a failed regression run; update staleness warnings appear in UI.

**Dependencies:** Phase 1 (link checker provides failure data), Phase 0 (refactor).

---

## Phase 5 — Always-On Setup + Scheduling

**Goal:** Run regression checks automatically on a schedule; access the dashboard from any device.

**Complexity:** M

**Blocker:** Michael is setting up an always-on Ubuntu machine. This phase happens once that machine is running.

**Tasks:**

*Remote access (do this before enabling always-on):*
- [ ] Install [Tailscale](https://tailscale.com) on Ubuntu machine, Mac, and iPhone (free personal plan)
- [ ] Dashboard becomes accessible at a stable Tailscale address from any device — no port forwarding needed
- [ ] Rate-limit `/api/unlock` endpoint (5 attempts / 60 seconds) as basic brute-force protection before going public on the tailnet
- [ ] Add Tailscale setup section to RUNBOOK

*Scheduling (once machine is running):*
- [ ] OS-level cron or Python `schedule` library triggers `/api/regression/run`
- [ ] Suggested cadence: nightly Layer 1 + Layer 3, weekly Layer 2 (visual diff), daily Layer 4 (URL snapshot)
- [ ] Last-run timestamps shown in regression UI

**Done criteria:** Tailscale running on all devices; dashboard accessible from iPhone; regression runs automatically on schedule.

**Dependencies:** Phase 4 (alerting, so you know when scheduled runs find problems).

---

## Phase 6 — Layer 5: Form Testing + Mailpit

**Goal:** Submit contact forms and checkout flows during regression runs without sending real emails to clients.

**Complexity:** XL

**Pre-requisite:** WSP plugin must be updated to support test mode.

**Tasks:**
- [ ] **WSP plugin:** Add `wsp_test_mode` flag to site options (toggled via REST endpoint)
- [ ] **WSP plugin:** `pre_wp_mail` filter intercepts all outbound email when test mode is active — logs to `wsp_intercepted_emails` DB table
- [ ] **WSP plugin endpoints:** `POST /wp-json/wsp/v1/test-mode` (activate/deactivate), `GET /wp-json/wsp/v1/test-mode/emails`
- [ ] **Dashboard:** Before regression run (if form testing enabled), activate test mode on each selected site
- [ ] **Mailpit binary** on dashboard server — local SMTP catcher with REST API
- [ ] **Playwright:** fill and submit configured forms per site; verify success state
- [ ] **Per-site config additions:** form URL + CSS selector, test input data template
- [ ] **After run:** deactivate test mode, fetch intercepted emails via Mailpit API; show in results UI

**Done criteria:** Regression run submits configured forms; intercepted emails visible in results; no real emails sent to clients.

**Dependencies:** Phase 2 (client/site model), Phase 5 (scheduling so form tests can run automatically).

---

## Phase 7 — Client-Facing Views

**Goal:** A shareable, read-only URL per client showing only their sites' data.

**Complexity:** L

**Tasks:**
- [ ] DB: `client_tokens` table — `(client_id, token_hash, created_at, last_used_at)`
- [ ] New route: `/client/{slug}` — read-only, token-authenticated
- [ ] Renders existing dashboard panels (Updates, Regression, Uptime) filtered to that client's sites
- [ ] No access to other clients' data, settings, or the server log
- [ ] Token generation UI in Settings

**Done criteria:** Shareable per-client URL shows correct filtered data; no access to other data; token can be rotated.

**Dependencies:** Phase 2 (client model), Phase 5 (Tailscale for HTTPS-equivalent transport).

---

## Parked (no phase — revisit when relevant)

- **Plugin/theme release dates** — WordPress.org API has partial data; premium plugins unresolvable; needs background enrichment job
- **PDF client report generation** — Monthly update summary PDF per client
- **Bulk update scheduling** — Trigger updates directly from dashboard via MainWP API
- **WISHLIST.md** — Full notes and research for all parked items

---

## Phase Summary

| Phase | What it delivers | Complexity | Blocked by |
|-------|-----------------|------------|-----------|
| 0 | Docs, refactor, UX fix | M | — |
| 1 | Broken link checker | M | Phase 0 |
| 2 | Client tagging + filtered views | M | — |
| 3 | URL snapshot + change tracking | M | Phase 0 |
| 4 | Alerting + notifications | S | Phase 1 |
| 5 | Always-on + Tailscale + scheduling | M | Hardware (Ubuntu machine) |
| 6 | Form testing + Mailpit | XL | Phase 2, WSP plugin work |
| 7 | Client-facing views | L | Phase 2, Phase 5 |
