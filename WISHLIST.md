# Dashboard Wishlist

Ideas and future features to revisit. Add context/notes when parking something here.

---

## Parked Items

### Plugin / Theme / WP Core Release Dates
**Goal:** Show how long a new release was available before we applied the update — useful for reporting to clients and spotting deferred-update patterns.

**Research notes:**
- WordPress.org public API (`/plugins/info/1.2/`, `/themes/info/1.2/`) has `last_updated` for current version and a `versions` dict, but no per-version release dates
- For the *current* version, `last_updated` is accurate; older versions would need SVN/trac scraping
- Premium plugins (Gravity Forms, ACF Pro, WooCommerce extensions, most page builders) are not on WordPress.org — no API available; would need manual input or vendor-specific APIs
- Recommended approach when revisited: background enrichment job — after sync, for each unique `(slug, new_version)` pair not already in cache, query WordPress.org API and store in a `release_dates` table

---

---

## Regression Testing Roadmap

Layer 1 (homepage HTTP checks + screenshots) is built. The items below are the planned next layers, in priority order.

### Layer 2: Visual Regression (Pixel Diff)
**Goal:** Compare each run's screenshots against a stored baseline to automatically detect visual changes after updates.

**Approach:** Self-hosted pixel diffing using `pixelmatch-py` (same engine Playwright uses internally). No third-party service — all data stays local.

**How it works:**
- Each site has a stored "baseline" screenshot (promoted manually by the user)
- On every regression run, diff the new screenshot against the baseline using pixelmatch
- Store a diff image (red-on-grey highlights showing changed pixels) and a similarity % score
- Surface pass/fail threshold in the UI (e.g. >1% diff = flag for review)

**UI additions needed:**
- "Set as Baseline" button per site in results view
- Side-by-side / overlay comparison viewer (baseline | diff | current)
- Similarity % column in results table
- Yellow/red highlight based on diff threshold

**Decision notes:**
- Evaluated Percy, Argos CI, Chromatic, BackstopJS — all ruled out (SaaS cost/quota, third-party screenshot storage, or Node.js dependency)
- `pixelmatch-py` is MIT licensed, pure Python, no external services

---

### Layer 3: Broken Link Checker
**Goal:** Detect broken links (4xx/5xx) across site pages, integrated into the regression run.

**Approach:** Extend Playwright regression run to extract all `<a href>` links on each page and issue HEAD/GET requests, logging status codes.

**Options evaluated:**
- `LinkChecker` Python library — pure Python, recursive crawl, CSV/HTML output, good fit
- `broken-link-checker` (Node.js npm) — ruled out (Node dependency)
- Dr. Link Check — **no API**, free tier limited to 2 sites (unsuitable), ruled out
- Screaming Frog — no API, desktop-only, ruled out

**Scope options to decide when building:**
- Homepage links only (fast, ~same cost as current Layer 1 run)
- Depth-2 crawl (follows internal links one level deep — slower but more thorough)
- Full site crawl (use `LinkChecker` library for recursive crawling)

**Storage:** New `broken_links` table per regression run, linked to `regression_results`

---

### Layer 4: URL Snapshot + Change Tracking
**Goal:** Export all URLs from each WP site, store snapshots over time, and report on any URLs that were added, removed, or changed between snapshots.

**Approach:**
- Parse `/wp-sitemap.xml` (built into WP 5.5+) or `/sitemap.xml` per site — no auth needed
- Alternatively, automate Michael's existing URL export script via WP-CLI or MainWP custom command runner
- Store URL lists per site per snapshot in a new `url_snapshots` table
- Diff consecutive snapshots: added / removed / changed (redirected, status changed)

**UI additions needed:**
- New "URL Changes" tab or section within Regression tab
- Snapshot history per site with diff view
- CSV export of changes

---

### Layer 5: Form Submission Testing + Email Interception
**Goal:** Submit contact forms, checkout flows, and other interactive forms during a regression run and verify they complete successfully — without sending real emails to clients.

**The interception problem:**
WordPress sends all email through `wp_mail()`. When a regression test submits a contact form, CF7/Gravity Forms/WooCommerce all call `wp_mail()`, which hits the site's live SMTP config and delivers email to real clients. This must be suppressed during test runs.

**Recommended approach: WSP Plugin test-mode + Mailpit**

Two components that compose well:

**Component 1 — WSP Plugin "Test Mode" endpoint:**
- Add a `wsp_test_mode` flag to each site's WP options (toggled via REST endpoint)
- When active, a `pre_wp_mail` filter intercepts all outbound email and either:
  - **Suppresses** the email entirely (returns `false` to abort sending), OR
  - **Redirects** all `$to` addresses to a single safe catch-all inbox, OR
  - **Logs** the full email payload (to, subject, body, headers) to a custom `wsp_intercepted_emails` DB table
- Expose REST endpoints:
  - `POST /wp-json/wsp/v1/test-mode {"active": true}` — activate before regression run
  - `POST /wp-json/wsp/v1/test-mode {"active": false}` — deactivate after run
  - `GET /wp-json/wsp/v1/test-mode/emails` — fetch intercepted emails for that run

**Component 2 — Mailpit (self-hosted SMTP catcher):**
- [Mailpit](https://mailpit.axllent.org/) is a tiny Go binary (or Docker image) that runs a local SMTP server and web UI — the modern replacement for MailHog
- During test mode, the WSP plugin temporarily overrides the site's SMTP to point at the Mailpit instance running on the dashboard server
- After the run, it restores the original SMTP config
- Mailpit's REST API lets the dashboard fetch caught emails and display them inline in regression results
- This is the better path if you want to verify email *content* (e.g. confirm the email was generated correctly), not just suppress it

**Dashboard flow:**
1. User starts regression run with "Form Testing" enabled
2. Dashboard calls `POST /wp-json/wsp/v1/test-mode {"active": true}` on each selected site
3. Playwright fills and submits each configured form on the site
4. Emails are caught by Mailpit / logged in WP — never reach real clients
5. After run, dashboard calls test-mode off, fetches intercepted emails via Mailpit API
6. Results UI shows form submission pass/fail + a collapsible "Intercepted Emails" section per site

**Per-site config additions needed:**
- List of forms to test per site (URL + CSS selector or form ID)
- Test input data (name, email, message — stored as a template, not real client data)
- Whether to use Mailpit redirection vs. full suppression

**Options evaluated:**
- **Mailtrap / SendGrid sandbox** — SaaS, requires SMTP config change on each site permanently, external data storage, ruled out
- **`wp_mail` suppression only** — simple but gives no visibility into whether the email was actually generated; fine for a basic smoke test but not for verifying form behavior
- **WSP plugin log-to-DB** — good self-contained option; no extra service to run; email payloads stored in WP and fetched by dashboard
- **Mailpit** — best option when you want a real UI for inspecting caught email and a REST API for the dashboard to consume

**Prerequisites:** WSP plugin must be installed on all client sites (already the case).

---

### Scheduling (Applies to All Layers)
**Goal:** Run regression checks automatically on a schedule rather than on-demand only.

**Notes:**
- ✅ Always-on Ubuntu machine is running at `100.77.172.38` with the dashboard deployed as a systemd service
- Scheduling is now unblocked — ready to build
- Suggested cadence: nightly Layer 1+3, weekly Layer 2 (visual diff), daily Layer 4 (URL snapshot)
- Will want email/webhook alerting when issues are found (separate wishlist item)

---

---

## Per-Site Configuration

**Goal:** Store site-specific settings that override global defaults — useful when individual sites have legitimate reasons to behave differently.

**Examples of what would be configurable per site:**
- Visual regression diff threshold (e.g. a site with heavy animations might tolerate 5% diff vs. the global 1%)
- Pages to exclude from link checking or URL snapshots
- Whether to run visual diff for this site at all (opt-out)
- Custom contact name / maintenance plan tier / internal notes
- Expected HTTP status (e.g. a staging site that intentionally returns 401)

**Approach:** New `site_config` table keyed by site ID (from MainWP). UI would be a settings panel or modal accessible from the site row in regression results. Low lift — no new pages needed, just a slide-out or modal form.

**Note:** This is a prerequisite for the client dashboard view below — you need the client/site relationship in the data model first.

---

## Client Dashboard Views

**Goal:** A filtered, per-client view of the dashboard — update history, regression results, and site health scoped to a single client's sites. Potentially shareable with the client directly.

**Two sub-versions to consider:**

**Internal view (easier):** A filtered mode within the existing dashboard — select a client/group and all tabs (Updates, Regression, etc.) filter to just their sites. No new auth, no separate URL. Just a client selector in the header that sets a global filter.

**Client-facing view (bigger lift):** A separate read-only URL per client (e.g. `/client/aprea-therapeutics`) with its own passcode that shows only that client's data. Useful for sharing with clients as a transparency/value-add. Requires:
- Client model in the DB (name, sites, access token)
- Read-only filtered rendering of existing dashboard panels
- Separate auth flow for client tokens vs. owner passphrase

**Prerequisites:**
- Per-site configuration (above) to establish the client/site relationship
- "Tag/group sites by client" — clients need to be modeled in the data before views can be scoped to them

**Suggested order:** Internal filtered view first (much simpler, still very useful), client-facing view later when the client model is mature.

---

## Other Potential Future Items

- Client-facing report generation (PDF summary of updates for a given period)
- Alerting / notifications when sites have pending updates older than N days
- Bulk update scheduling directly from the dashboard
- Tag/group sites by client tier or maintenance plan
- Email/webhook alerts when regression runs find issues
