// ============================================================================
// Link Checker
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { escapeHtml, modalPush, modalRemove, _sortTh, downloadCsv } from './dom.js';
import { buildHash, navigate, _saveLastSubview, registerRoute } from './router.js';

import { dbg } from './debug.js';
dbg('module', 'linkcheck.js loaded');

        // Link Checker
        // ============================================================================
        // Data shape from API:
        //   /api/linkcheck/results/<id>  → { id, started_at, …, sites: [{site_id, site_name,
        //       site_url, pages_crawled, links_checked, broken_count, broken_links: […]}, …] }
        //   /api/linkcheck/site-status   → [{site_id, site_name, site_url,
        //       pages_crawled, links_checked, broken_count, run_id, run_started_at}, …]
        //   /api/linkcheck/site/<id>/history → [{run_id, started_at, …,
        //       pages_crawled, links_checked, broken_count}, …]
        //   /api/linkcheck/results/<run_id>/site/<site_id> → [{…broken link rows…}]

        let _lcViewMode = 'sites';        // 'sites' | 'runs'
        let _lcRuns     = [];             // all runs (newest first)
        let _lcSelectedRunId = null;
        let _lcPollTimer = null;
        let _lcSiteList  = [];            // registered sites for selector
        let _lcSelectedIds = new Set();   // currently selected site IDs (strings)
        let _lcExpandedSites = new Set(); // site keys with expanded rows (runs view)
        let _lcLatestRunMeta = null;      // metadata from latest completed run

        // Export state — snapshot of last-rendered data for CSV download
        let _lcLastSiteStatusData = {};   // statusById from last renderLcSiteStatusTable
        let _lcLastRunData = null;        // run object from last renderLcRunsView
        let _lcLastHistoryData = [];      // rows from last loadLcSiteHistory
        let _lcCurrentHistorySite = { id: null, name: '', url: '' };
        let _lcHistoryRowMap = {};        // expandId → history row; rebuilt on each renderLcSiteHistoryBody

        // Column sort state for each LC table
        // dir: 1 = ascending (▲), -1 = descending (▼)
        let _lcSiteSort    = { col: 'broken',  dir: -1 }; // default: broken desc
        let _lcRunsSort    = { col: 'broken',  dir: -1 };
        let _lcHistorySort = { col: 'date',    dir: -1 }; // newest first

        // Link scope toggles (persisted to localStorage)
        let _lcCheckInternal = true;
        let _lcCheckExternal = false;

        const LC_STORAGE_KEY  = 'lc_selected_sites';
        const LC_SCOPE_KEY    = 'lc_link_scope';
        const LC_DEFAULTS_KEY = 'lc_default_options';

        let _lcSetDefaultTimer = null; // auto-cancel timer for Set Default confirm step

        // ── Tiny helpers ─────────────────────────────────────────────────────────
        async function lcGet(path) {
            const r = await fetch(path);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        }
        async function lcPost(path, body = {}) {
            const r = await fetch(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            return r.json();
        }

        // ── View toggle ───────────────────────────────────────────────────────────
        function setLcView(mode) {
            _lcViewMode = mode;
            document.getElementById('lcViewSites').className =
                mode === 'sites' ? 'btn btn-primary' : 'btn';
            document.getElementById('lcViewRuns').className =
                mode === 'runs' ? 'btn btn-primary' : 'btn';
            // Run selector only visible in Runs mode
            document.getElementById('lcRunSelect').style.display =
                mode === 'runs' ? '' : 'none';
            document.getElementById('lcRunInfo').textContent = '';
            // Hide history panel whenever view switches
            document.getElementById('lcSiteHistoryPanel').style.display = 'none';
            document.getElementById('lcTableWrap').style.display = '';
            if (mode === 'sites') {
                loadLcSiteStatus();
            } else {
                loadLcRuns();
                if (_lcSelectedRunId) loadLcRunResults(_lcSelectedRunId);
                else loadLcRunResults(null);
            }
        }

        document.getElementById('lcViewSites').addEventListener('click', () => {
            history.replaceState(null, '', buildHash({ tab: 'linkchecker', subview: 'sites' }));
            _saveLastSubview('linkchecker', 'sites');
            setLcView('sites');
        });
        document.getElementById('lcViewRuns').addEventListener('click', () => {
            history.replaceState(null, '', buildHash({ tab: 'linkchecker', subview: 'runs' }));
            _saveLastSubview('linkchecker', 'runs');
            setLcView('runs');
        });

        // ── Entry point ───────────────────────────────────────────────────────────
        async function loadLinkChecker() {
            // Restore persisted scope toggles
            try {
                const saved = JSON.parse(localStorage.getItem(LC_SCOPE_KEY) || 'null');
                if (saved && typeof saved === 'object') {
                    _lcCheckInternal = saved.internal !== false; // default true
                    _lcCheckExternal = !!saved.external;
                }
            } catch { /* ignore */ }
            syncLcScopeButtons();

            // Load site list for the selector (shared with runs)
            await loadLcSiteList();

            // Check for in-progress run
            const status = await lcGet('/api/linkcheck/status').catch(() => ({}));
            if (status.active_run) {
                lcShowProgress(status.active_run);
                startLcPoll();
            } else {
                lcHideProgress();
            }

            // Load the active view
            if (_lcViewMode === 'sites') {
                loadLcSiteStatus();
            } else {
                loadLcRuns();
                loadLcRunResults(_lcSelectedRunId);
            }
        }

        // ── Sites view ────────────────────────────────────────────────────────────
        async function loadLcSiteStatus() {
            document.getElementById('lcSiteHistoryPanel').style.display = 'none';
            document.getElementById('lcTableWrap').style.display = '';

            try {
                const [siteStatus, runs] = await Promise.all([
                    lcGet('/api/linkcheck/site-status'),
                    lcGet('/api/linkcheck/runs').catch(() => []),
                ]);
                _lcRuns = runs;
                renderLcRunSelector();
                _lcLatestRunMeta = runs.find(r => r.status === 'completed') || null;

                const statusById = {};
                for (const s of siteStatus) statusById[String(s.site_id)] = s;
                updateLcSummaryFromSiteStatus(siteStatus);
                renderLcSiteStatusTable(statusById);
                if (_pendingLcSiteHistory) {
                    const sid = _pendingLcSiteHistory;
                    _pendingLcSiteHistory = null;
                    const site = _lcSiteList.find(s => String(s.id) === sid);
                    openLcSiteHistory(sid, site?.name || '', site?.url || '');
                }
            } catch (e) {
                renderLcTableError(`Error loading site status: ${e.message}`);
            }
        }

        // ── LC sort helpers ───────────────────────────────────────────────────────

        function _sortLcSites(sites, statusById) {
            const { col, dir } = _lcSiteSort;
            if (!col) return sites;
            return [...sites].sort((a, b) => {
                const sa = statusById[String(a.id)] || {};
                const sb = statusById[String(b.id)] || {};
                let av, bv;
                switch (col) {
                    case 'site':    av = (a.name || '').toLowerCase(); bv = (b.name || '').toLowerCase(); break;
                    case 'checked': av = sa.run_started_at || ''; bv = sb.run_started_at || ''; break;
                    case 'links':   av = sa.links_checked  ?? -1; bv = sb.links_checked  ?? -1; break;
                    case 'working': av = (sa.links_checked ?? 0) - (sa.broken_count ?? 0);
                                    bv = (sb.links_checked ?? 0) - (sb.broken_count ?? 0); break;
                    case 'broken':  av = sa.broken_count   ?? -1; bv = sb.broken_count   ?? -1; break;
                    default: return 0;
                }
                return av < bv ? -dir : av > bv ? dir : 0;
            });
        }

        function _sortLcRuns(sites) {
            const { col, dir } = _lcRunsSort;
            if (!col) return sites;
            return [...sites].sort((a, b) => {
                let av, bv;
                switch (col) {
                    case 'site':    av = (a.site_name || '').toLowerCase(); bv = (b.site_name || '').toLowerCase(); break;
                    case 'links':   av = a.links_checked ?? -1; bv = b.links_checked ?? -1; break;
                    case 'working': av = (a.links_checked ?? 0) - (a.broken_count ?? 0);
                                    bv = (b.links_checked ?? 0) - (b.broken_count ?? 0); break;
                    case 'broken':  av = a.broken_count  ?? -1; bv = b.broken_count  ?? -1; break;
                    default: return 0;
                }
                return av < bv ? -dir : av > bv ? dir : 0;
            });
        }

        function _sortLcHistory(rows) {
            const { col, dir } = _lcHistorySort;
            if (!col) return rows;
            return [...rows].sort((a, b) => {
                let av, bv;
                switch (col) {
                    case 'date':    av = a.started_at || ''; bv = b.started_at || ''; break;
                    case 'links':   av = a.links_checked ?? -1; bv = b.links_checked ?? -1; break;
                    case 'working': av = (a.links_checked ?? 0) - (a.broken_count ?? 0);
                                    bv = (b.links_checked ?? 0) - (b.broken_count ?? 0); break;
                    case 'broken':  av = a.broken_count  ?? -1; bv = b.broken_count  ?? -1; break;
                    default: return 0;
                }
                return av < bv ? -dir : av > bv ? dir : 0;
            });
        }

        function renderLcSiteStatusTable(statusById) {
            _lcLastSiteStatusData = statusById; // snapshot for CSV export
            const body = document.getElementById('lcBody');
            const empty = document.getElementById('lcEmpty');

            // Generate sortable thead
            const thead = document.getElementById('lcThead');
            thead.innerHTML = `<tr>
                <th style="width:40px;">Status</th>
                ${_sortTh('Site', 'site', _lcSiteSort)}
                ${_sortTh('Last Checked', 'checked', _lcSiteSort, 'width:130px;')}
                ${_sortTh('Links', 'links', _lcSiteSort, 'text-align:right; width:80px;')}
                ${_sortTh('Working', 'working', _lcSiteSort, 'text-align:right; width:80px;')}
                ${_sortTh('Broken', 'broken', _lcSiteSort, 'text-align:right; width:80px;')}
            </tr>`;
            thead.querySelectorAll('th[data-sort-col]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.sortCol;
                    if (_lcSiteSort.col === col) _lcSiteSort.dir *= -1;
                    else { _lcSiteSort.col = col; _lcSiteSort.dir = 1; }
                    renderLcSiteStatusTable(_lcLastSiteStatusData);
                });
            });

            const activeSites = _sortLcSites(_lcSiteList.filter(s => !s.is_removed), statusById);
            if (!activeSites.length && !Object.keys(statusById).length) {
                body.innerHTML = '';
                empty.textContent = 'No link check runs yet. Click "Run Link Check" to start.';
                empty.style.display = '';
                return;
            }
            empty.style.display = 'none';

            const rows = activeSites.map(site => {
                const st = statusById[String(site.id)];
                const hasRun     = !!st;
                const total      = st ? (st.links_checked  ?? 0) : null;
                const broken     = st ? (st.broken_count   ?? 0) : null;
                const working    = (total !== null && broken !== null) ? (total - broken) : null;
                const prevBroken = st?.prev_broken_count  ?? null;
                const prevLinks  = st?.prev_links_checked ?? null;
                const prevWorking = (prevLinks !== null && prevBroken !== null) ? prevLinks - prevBroken : null;

                const icon = !hasRun ? `<span style="color:var(--text-muted);">—</span>`
                    : broken > 0     ? `<span style="color:var(--red);">⚠</span>`
                                     : `<span style="color:var(--green);">✓</span>`;

                const fmt = n => n === null ? `<span style="color:var(--text-muted);">—</span>`
                                            : n.toLocaleString();
                const brokenCell = broken === null ? fmt(null)
                    : (broken > 0
                        ? `<span style="color:var(--red); font-weight:600;">${broken.toLocaleString()}</span>`
                        : `<span style="color:var(--green);">0</span>`)
                      + lcDeltaBadge(broken, prevBroken, true);
                const workingCell = working === null ? fmt(null)
                    : `<span style="color:${working === total ? 'var(--green)' : 'var(--text)'};">${working.toLocaleString()}</span>`
                      + lcDeltaBadge(working, prevWorking, false);

                let lastChecked = '—';
                if (st?.run_started_at) {
                    const d = new Date(st.run_started_at + 'Z');
                    const ago = Date.now() - d.getTime();
                    if (ago < 3600000) lastChecked = `${Math.round(ago / 60000)}m ago`;
                    else if (ago < 86400000) lastChecked = `${Math.round(ago / 3600000)}h ago`;
                    else lastChecked = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                }

                const domain = (site.url || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
                const rowBg  = (broken ?? 0) > 0 ? 'background:var(--yellow-bg);' : '';

                return `<tr style="cursor:pointer; ${rowBg}"
                        data-site-id="${site.id}"
                        data-site-name="${escapeHtml(site.name || '')}"
                        data-site-url="${escapeHtml(site.url || '')}">
                    <td style="text-align:center;">${icon}</td>
                    <td>
                        <div style="font-weight:500;">${escapeHtml(site.name || '—')} <span style="color:var(--text-muted); font-size:11px;">›</span></div>
                        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml(domain)}</div>
                    </td>
                    <td style="font-size:12px; color:var(--text-muted);">${lastChecked}</td>
                    <td style="text-align:right; font-size:13px;">${fmt(total)}</td>
                    <td style="text-align:right; font-size:13px;">${workingCell}</td>
                    <td style="text-align:right; font-size:13px;">${brokenCell}</td>
                </tr>`;
            });

            body.innerHTML = rows.join('');

            body.querySelectorAll('tr[data-site-id]').forEach(row => {
                row.addEventListener('click', () => {
                    navigate({ tab: 'linkchecker', subview: 'sites', id: row.dataset.siteId });
                });
            });
        }

        // ── Per-site history ──────────────────────────────────────────────────────
        function openLcSiteHistory(siteId, siteName, siteUrl) {
            _lcCurrentHistorySite = { id: siteId, name: siteName || '', url: siteUrl || '' };
            document.getElementById('lcTableWrap').style.display = 'none';
            document.getElementById('lcSummary').style.display = 'none';
            document.getElementById('lcSiteHistoryPanel').style.display = '';
            document.getElementById('lcSiteHistoryTitle').textContent = siteName || '';
            document.getElementById('lcSiteHistoryUrl').textContent =
                (siteUrl || '').replace(/^https?:\/\//, '');
            loadLcSiteHistory(siteId);
        }

        function closeLcSiteHistory() {
            _lcCurrentHistorySite = { id: null, name: '', url: '' };
            history.replaceState(null, '', buildHash({ tab: 'linkchecker', subview: 'sites' }));
            document.getElementById('lcSiteHistoryPanel').style.display = 'none';
            document.getElementById('lcTableWrap').style.display = '';
            if (_lcLatestRunMeta) {
                document.getElementById('lcSummary').style.display = '';
            }
        }

        document.getElementById('lcSiteHistoryBackBtn').addEventListener('click', () => {
            navigate({ tab: 'linkchecker', subview: 'sites' });
        });

        async function loadLcSiteHistory(siteId) {
            const tbody = document.getElementById('lcSiteHistoryBody');
            const COLS  = 5; // status + date + links + working + broken
            tbody.innerHTML = `<tr><td colspan="${COLS}" style="color:var(--text-muted); padding:20px; text-align:center;">Loading…</td></tr>`;
            document.getElementById('lcSiteHistoryEmpty').style.display = 'none';
            try {
                const history = await lcGet(`/api/linkcheck/site/${siteId}/history`);
                _lcLastHistoryData = history; // snapshot for CSV export + sort re-renders
                renderLcSiteHistoryBody(history, siteId);
            } catch (e) {
                tbody.innerHTML = `<tr><td colspan="5" style="color:var(--red); padding:16px; text-align:center;">
                    Error: ${escapeHtml(e.message)}</td></tr>`;
            }
        }

        // target defaults to the main-page history panel ids. Pass alternate ids for Site Dashboard.
        // Safe to share _lcHistoryRowMap because the main-page panel and Site Dashboard are on
        // different tabs and can never both be visible at the same time.
        export function renderLcSiteHistoryBody(history, siteId, target) {
            const t = target || { tbodyId: 'lcSiteHistoryBody', theadId: 'lcSiteHistoryThead', emptyId: 'lcSiteHistoryEmpty' };
            const tbody = document.getElementById(t.tbodyId);
            const empty = document.getElementById(t.emptyId);
            const COLS  = 5;

            // Generate sortable thead
            const thead = document.getElementById(t.theadId);
            thead.innerHTML = `<tr>
                <th style="width:40px;">Status</th>
                ${_sortTh('Date', 'date', _lcHistorySort)}
                ${_sortTh('Links', 'links', _lcHistorySort, 'text-align:right; width:80px;')}
                ${_sortTh('Working', 'working', _lcHistorySort, 'text-align:right; width:80px;')}
                ${_sortTh('Broken', 'broken', _lcHistorySort, 'text-align:right; width:80px;')}
            </tr>`;
            thead.querySelectorAll('th[data-sort-col]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.sortCol;
                    if (_lcHistorySort.col === col) _lcHistorySort.dir *= -1;
                    else { _lcHistorySort.col = col; _lcHistorySort.dir = 1; }
                    renderLcSiteHistoryBody(_lcLastHistoryData, _lcCurrentHistorySite.id, target);
                });
            });

            if (!history.length) {
                tbody.innerHTML = '';
                empty.style.display = '';
                return;
            }
            empty.style.display = 'none';

            // Rebuild expand-row map keyed by run_id (stable across re-sorts)
            _lcHistoryRowMap = {};

            let html = '';
            _sortLcHistory(history).forEach(r => {
                const d = new Date((r.started_at || '') + 'Z');
                const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                    + ' at ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                const total      = r.links_checked  ?? 0;
                const broken     = r.broken_count   ?? 0;
                const working    = total - broken;
                const prevBroken  = r.prev_broken_count  ?? null;
                const prevLinks   = r.prev_links_checked ?? null;
                const prevWorking = (prevLinks !== null && prevBroken !== null) ? prevLinks - prevBroken : null;

                const icon = broken > 0
                    ? `<span style="color:var(--red);">⚠</span>`
                    : `<span style="color:var(--green);">✓</span>`;
                const brokenCell = (broken > 0
                    ? `<span style="color:var(--red); font-weight:600;">${broken}</span>`
                    : `<span style="color:var(--green);">0</span>`)
                    + lcDeltaBadge(broken, prevBroken, true);
                const workingCell = `<span style="color:${working === total ? 'var(--green)' : 'var(--text)'};">${working.toLocaleString()}</span>`
                    + lcDeltaBadge(working, prevWorking, false);
                const rowBg    = broken > 0 ? 'background:var(--yellow-bg);' : '';
                const expandId = `lc-hist-expand-${r.run_id}`;
                const expandArrow = broken > 0
                    ? `<span style="font-size:11px; color:var(--text-muted); margin-left:4px;">▼</span>`
                    : '';

                html += `<tr class="lc-site-row" style="cursor:${broken > 0 ? 'pointer' : 'default'}; ${rowBg}"
                        data-run-id="${r.run_id}" data-site-id="${escapeHtml(String(siteId))}" data-expand-id="${expandId}">
                    <td style="text-align:center;">${icon}</td>
                    <td style="font-size:13px;">${dateStr}</td>
                    <td style="text-align:right;">${total.toLocaleString()}</td>
                    <td style="text-align:right;">${workingCell}</td>
                    <td style="text-align:right;">${brokenCell}${expandArrow}</td>
                </tr>`;

                if (broken > 0) {
                    // Expand row — plain <tr> (no nested tbody); content lazy-loaded on first click
                    _lcHistoryRowMap[expandId] = r;
                    html += `<tr id="${expandId}" class="lc-expand" data-loaded="false">
                        <td colspan="${COLS}" class="lc-expand-cell"></td>
                    </tr>`;
                }
            });

            tbody.innerHTML = html;

            tbody.querySelectorAll('tr[data-run-id]').forEach(row => {
                row.addEventListener('click', async () => {
                    const runId      = row.dataset.runId;
                    const thisSiteId = row.dataset.siteId;
                    const expandId   = row.dataset.expandId;
                    if (!expandId) return;
                    const expandEl = document.getElementById(expandId);
                    if (!expandEl) return;

                    const isOpen = expandEl.classList.toggle('open');
                    const arrow  = row.querySelector('td:nth-child(5) span:last-child');
                    if (arrow && arrow.textContent.trim().match(/[▼▲]/)) {
                        arrow.textContent = isOpen ? ' ▲' : ' ▼';
                    }
                    if (!isOpen) return;

                    // Lazy-load broken links + stats bar the first time
                    if (expandEl.dataset.loaded !== 'true') {
                        const cell = expandEl.querySelector('td');
                        cell.innerHTML = `<div class="lc-broken-row" style="text-align:center; color:var(--text-muted); padding:10px;">Loading…</div>`;
                        try {
                            const links   = await lcGet(`/api/linkcheck/results/${runId}/site/${thisSiteId}`);
                            const siteRow = _lcHistoryRowMap[expandId] || {};
                            cell.innerHTML = lcStatsBarHtml(siteRow, links) + lcBrokenLinksHtml(links);
                            expandEl.dataset.loaded = 'true';
                        } catch (e) {
                            cell.innerHTML = `<div class="lc-broken-row" style="color:var(--red); padding:10px;">${escapeHtml(e.message)}</div>`;
                        }
                    }
                });
            });
        }

        // ── Runs view ─────────────────────────────────────────────────────────────
        async function loadLcRuns() {
            try {
                _lcRuns = await lcGet('/api/linkcheck/runs');
            } catch { _lcRuns = []; }
            renderLcRunSelector();
        }

        function renderLcRunSelector() {
            const sel = document.getElementById('lcRunSelect');
            if (!_lcRuns.length) { sel.style.display = 'none'; return; }
            if (_lcViewMode === 'runs') sel.style.display = '';
            const prevVal = _lcSelectedRunId ? String(_lcSelectedRunId) : null;
            sel.innerHTML = _lcRuns.map(r => {
                const d = new Date((r.started_at || '') + 'Z');
                const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                    + ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                    + ` — ${r.total_broken ?? 0} broken`
                    + (r.status !== 'completed' ? ` (${r.status})` : '');
                return `<option value="${r.id}">${label}</option>`;
            }).join('');
            if (prevVal && sel.querySelector(`option[value="${prevVal}"]`)) {
                sel.value = prevVal;
            }
        }

        document.getElementById('lcRunSelect').addEventListener('change', e => {
            history.replaceState(null, '', buildHash({ tab: 'linkchecker', subview: 'runs', id: e.target.value }));
            loadLcRunResults(e.target.value);
        });

        async function loadLcRunResults(runId) {
            if (runId) _lcSelectedRunId = String(runId);
            const sel = document.getElementById('lcRunSelect');
            if (runId && sel.value !== String(runId)) sel.value = String(runId);

            try {
                const url = runId
                    ? `/api/linkcheck/results/${runId}`
                    : '/api/linkcheck/latest';
                const data = await lcGet(url);
                _lcSelectedRunId = String(data.id || runId || '');
                if (sel.value !== _lcSelectedRunId && sel.querySelector(`option[value="${_lcSelectedRunId}"]`)) {
                    sel.value = _lcSelectedRunId;
                }
                renderLcRunsView(data);
                await loadLcRuns(); // refresh selector options
            } catch {
                renderLcEmpty();
            }
        }

        // ── Delta badge helper ────────────────────────────────────────────────────
        /**
         * Returns a small inline badge like "ꜛ4" or "ꜜ3" showing the change
         * from prev to current.
         * lowerIsBetter: true for Broken (a drop is green), false for Working.
         * Returns '' when there's no previous data or no change.
         */
        function lcDeltaBadge(current, prev, lowerIsBetter) {
            if (prev === null || prev === undefined) return '';
            const delta = current - prev;
            if (delta === 0) return '';
            const up   = delta > 0;
            const good = lowerIsBetter ? !up : up;
            const color = good ? 'var(--green)' : 'var(--red)';
            const arrow = up ? 'ꜛ' : 'ꜜ';
            return `<span style="font-size:10px; color:${color}; margin-left:4px; font-weight:700; letter-spacing:0;">${arrow}${Math.abs(delta).toLocaleString()}</span>`;
        }

        // ── Stats bar helper ──────────────────────────────────────────────────────
        /**
         * Renders a compact stats strip above the broken links list.
         * `site`  — the site summary row (from link_check_site_runs)
         * `links` — the broken links array (already loaded)
         */
        function lcStatsBarHtml(site, links) {
            const extCount      = site.external_count    ?? 0;
            const redirectCount = site.redirect_count    ?? 0;
            const imageCount    = site.image_link_count  ?? 0;
            const broken        = site.broken_count      ?? links.length;

            if (!extCount && !redirectCount && !imageCount && !broken) return '';

            const n404     = links.filter(l => l.status_code === 404).length;
            const n5xx     = links.filter(l => l.status_code >= 500 && l.status_code < 600).length;
            const nTimeout = links.filter(l => (l.error || '').toLowerCase() === 'timeout').length;
            const nOther   = broken - n404 - n5xx - nTimeout;

            const pills = [];
            if (extCount)      pills.push(`<span>↗ ${extCount.toLocaleString()} external</span>`);
            if (redirectCount) pills.push(`<span>↪ ${redirectCount.toLocaleString()} redirect${redirectCount !== 1 ? 's' : ''}</span>`);
            if (imageCount)    pills.push(`<span>🖼 ${imageCount.toLocaleString()} image link${imageCount !== 1 ? 's' : ''}</span>`);
            if (broken > 0) {
                const bp = [];
                if (n404)     bp.push(`404: ${n404}`);
                if (n5xx)     bp.push(`5xx: ${n5xx}`);
                if (nTimeout) bp.push(`timeout: ${nTimeout}`);
                if (nOther > 0) bp.push(`other: ${nOther}`);
                if (bp.length) pills.push(`<span style="color:var(--red);">⚠ ${bp.join(' · ')}</span>`);
            }
            if (!pills.length) return '';
            return `<div style="display:flex; gap:16px; flex-wrap:wrap; align-items:center;
                        padding:7px 14px; font-size:12px; color:var(--text-muted);
                        border-bottom:1px solid var(--border); background:var(--surface-2);">
                ${pills.join('')}
            </div>`;
        }

        // ── Shared helper: render a list of broken link rows ─────────────────────
        // Returns <div class="lc-broken-row"> elements — used inside a <td> wrapper,
        // not directly as <tr> elements, avoiding the invalid nested-tbody pattern.
        function lcBrokenLinksHtml(links) {
            if (!links.length) {
                return `<div class="lc-broken-row" style="text-align:center; color:var(--green);">
                    ✓ No broken links
                </div>`;
            }
            return links.map(link => {
                const statusLabel = link.status_code
                    ? `<span style="font-weight:600; color:var(--red);">${link.status_code}</span>`
                    : `<span style="color:var(--text-muted);">${escapeHtml(link.error || 'Error')}</span>`;
                const badge = link.is_external
                    ? `<span class="lc-badge lc-badge-external">External</span>`
                    : `<span class="lc-badge lc-badge-internal">Internal</span>`;
                const imageBadge = link.is_image
                    ? `<span class="lc-badge" style="background:rgba(251,191,36,0.12); border-color:var(--yellow); color:var(--yellow);">Image</span>`
                    : '';
                const srcShort  = (link.source_page || '').replace(/^https?:\/\/[^/]+/, '').slice(0, 60) || '/';
                const linkShort = (link.link_url || '').replace(/^https?:\/\/[^/]+/, '').slice(0, 80) || link.link_url;
                return `<div class="lc-broken-row">
                    <div style="display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;">
                        ${statusLabel}
                        ${badge}${imageBadge}
                        <a href="${escapeHtml(link.link_url || '#')}" target="_blank" rel="noopener"
                           style="color:var(--accent); font-size:12px; word-break:break-all;"
                           title="${escapeHtml(link.link_url || '')}">${escapeHtml(linkShort)}</a>
                    </div>
                    <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                        Found on:
                        <a href="${escapeHtml(link.source_page || '#')}" target="_blank" rel="noopener"
                           style="color:var(--text-muted);">${escapeHtml(srcShort)}</a>
                    </div>
                </div>`;
            }).join('');
        }

        function renderLcRunsView(run) {
            _lcLastRunData = run; // snapshot for CSV export
            const sites = run.sites || [];
            const body  = document.getElementById('lcBody');
            const empty = document.getElementById('lcEmpty');
            const COLS  = 5; // status + site + links + broken + working

            // Generate sortable thead
            const thead = document.getElementById('lcThead');
            thead.innerHTML = `<tr>
                <th style="width:40px;">Status</th>
                ${_sortTh('Site', 'site', _lcRunsSort)}
                ${_sortTh('Links', 'links', _lcRunsSort, 'text-align:right; width:80px;')}
                ${_sortTh('Working', 'working', _lcRunsSort, 'text-align:right; width:80px;')}
                ${_sortTh('Broken', 'broken', _lcRunsSort, 'text-align:right; width:80px;')}
            </tr>`;
            thead.querySelectorAll('th[data-sort-col]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.sortCol;
                    if (_lcRunsSort.col === col) _lcRunsSort.dir *= -1;
                    else { _lcRunsSort.col = col; _lcRunsSort.dir = 1; }
                    renderLcRunsView(_lcLastRunData);
                });
            });

            updateLcSummaryCards(run);

            if (run.finished_at) {
                const d = new Date(run.finished_at + 'Z');
                document.getElementById('lcRunInfo').textContent =
                    `Finished ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} at ${d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
            }

            if (!sites.length) {
                body.innerHTML = '';
                empty.textContent = 'No results for this run.';
                empty.style.display = '';
                return;
            }
            empty.style.display = 'none';

            let html = '';
            _sortLcRuns(sites).forEach((site, idx) => {
                const total      = site.links_checked  ?? 0;
                const broken     = site.broken_count   ?? 0;
                const working    = total - broken;
                const prevBroken  = site.prev_broken_count  ?? null;
                const prevLinks   = site.prev_links_checked ?? null;
                const prevWorking = (prevLinks !== null && prevBroken !== null) ? prevLinks - prevBroken : null;
                const hasBroken  = broken > 0;
                const key        = String(site.site_id ?? site.site_name ?? idx);
                const isExpanded = _lcExpandedSites.has(key);
                const domain     = (site.site_url || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
                const rowBg = hasBroken ? 'background:var(--yellow-bg);' : '';
                const icon  = hasBroken ? `<span style="color:var(--red);">⚠</span>`
                                        : `<span style="color:var(--green);">✓</span>`;
                const brokenCell  = (hasBroken
                    ? `<span style="color:var(--red); font-weight:600;">${broken}</span>`
                    : `<span style="color:var(--green);">0</span>`)
                    + lcDeltaBadge(broken, prevBroken, true);
                const workingCell = `<span style="color:${working === total ? 'var(--green)' : 'var(--text)'};">${working.toLocaleString()}</span>`
                    + lcDeltaBadge(working, prevWorking, false);
                const expandArrow = hasBroken
                    ? `<span style="font-size:11px; color:var(--text-muted); margin-left:4px;">${isExpanded ? '▲' : '▼'}</span>`
                    : '';

                html += `<tr class="lc-site-row" style="cursor:${hasBroken ? 'pointer' : 'default'}; ${rowBg}"
                        data-site-key="${escapeHtml(key)}">
                    <td style="text-align:center;">${icon}</td>
                    <td>
                        <div style="font-weight:500;">${escapeHtml(site.site_name || '—')}</div>
                        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml(domain)}</div>
                    </td>
                    <td style="text-align:right; font-size:13px;">${total.toLocaleString()}</td>
                    <td style="text-align:right; font-size:13px;">${workingCell}</td>
                    <td style="text-align:right; font-size:13px;">${brokenCell}${expandArrow}</td>
                </tr>`;

                if (hasBroken) {
                    // Expand row — plain <tr> (no nested tbody) avoids invalid HTML.
                    // broken_links are pre-loaded from the API response.
                    const preloaded = site.broken_links || [];
                    html += `<tr class="lc-expand${isExpanded ? ' open' : ''}" data-site-key="${escapeHtml(key)}" data-loaded="true">
                        <td colspan="${COLS}" class="lc-expand-cell">
                            ${lcStatsBarHtml(site, preloaded)}${lcBrokenLinksHtml(preloaded)}
                        </td>
                    </tr>`;
                }
            });

            body.innerHTML = html;

            body.querySelectorAll('.lc-site-row').forEach(row => {
                row.addEventListener('click', () => {
                    const key = row.dataset.siteKey;
                    const expandEl = body.querySelector(`.lc-expand[data-site-key="${CSS.escape(key)}"]`);
                    if (!expandEl) return;
                    const arrow = row.querySelector('td:nth-child(5) span:last-child');
                    const isOpen = expandEl.classList.toggle('open');
                    if (isOpen) _lcExpandedSites.add(key); else _lcExpandedSites.delete(key);
                    if (arrow && arrow.textContent.trim().match(/[▼▲]/)) {
                        arrow.textContent = isOpen ? ' ▲' : ' ▼';
                    }
                });
            });
        }

        // ── Summary cards helpers ─────────────────────────────────────────────────

        /** Used by Runs view — totals come directly from the single run record. */
        function updateLcSummaryCards(run) {
            document.getElementById('lcStatSites').textContent = run.total_sites ?? '—';
            document.getElementById('lcStatPages').textContent = (run.total_pages_crawled ?? 0).toLocaleString();
            document.getElementById('lcStatLinks').textContent = (run.total_links_checked ?? 0).toLocaleString();
            const broken = run.total_broken ?? 0;
            const el = document.getElementById('lcStatBroken');
            el.textContent = broken.toLocaleString();
            el.style.color = broken > 0 ? 'var(--red)' : 'var(--green)';
            document.getElementById('lcSummary').style.display = '';
        }

        /**
         * Used by Sites view — aggregates across each site's own most recent run
         * so the cards reflect what the table shows, not a single global run.
         */
        function updateLcSummaryFromSiteStatus(siteStatusList) {
            if (!siteStatusList.length) {
                document.getElementById('lcSummary').style.display = 'none';
                return;
            }
            const totalPages  = siteStatusList.reduce((s, r) => s + (r.pages_crawled  ?? 0), 0);
            const totalLinks  = siteStatusList.reduce((s, r) => s + (r.links_checked  ?? 0), 0);
            const totalBroken = siteStatusList.reduce((s, r) => s + (r.broken_count   ?? 0), 0);
            document.getElementById('lcStatSites').textContent = siteStatusList.length.toLocaleString();
            document.getElementById('lcStatPages').textContent = totalPages.toLocaleString();
            document.getElementById('lcStatLinks').textContent = totalLinks.toLocaleString();
            const el = document.getElementById('lcStatBroken');
            el.textContent = totalBroken.toLocaleString();
            el.style.color = totalBroken > 0 ? 'var(--red)' : 'var(--green)';
            document.getElementById('lcSummary').style.display = '';
        }

        function renderLcEmpty() {
            document.getElementById('lcSummary').style.display = 'none';
            document.getElementById('lcBody').innerHTML = '';
            document.getElementById('lcEmpty').textContent = 'No link check runs yet. Click "Run Link Check" to start.';
            document.getElementById('lcEmpty').style.display = '';
        }

        function renderLcTableError(msg) {
            document.getElementById('lcBody').innerHTML =
                `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">${escapeHtml(msg)}</td></tr>`;
        }

        // ── Progress bar ──────────────────────────────────────────────────────────
        function lcShowProgress(activeRun) {
            document.getElementById('lcProgress').style.display = '';
            document.getElementById('lcRunBtn').style.display = 'none';
            const cancelBtn = document.getElementById('lcCancelBtn');
            cancelBtn.style.display = '';
            cancelBtn.disabled = false;

            const cur = activeRun.checked_sites ?? 0;
            const tot = activeRun.total_sites ?? 0;
            const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;
            document.getElementById('lcProgressBar').style.width = pct + '%';
            document.getElementById('lcProgressLabel').textContent =
                activeRun.current_site ? `Checking ${activeRun.current_site}…` : 'Preparing…';
            document.getElementById('lcProgressCount').textContent =
                tot > 0 ? `${cur} / ${tot}` : '';

            // Live summary
            document.getElementById('lcStatSites').textContent = tot || '—';
            document.getElementById('lcStatLinks').textContent = (activeRun.total_links ?? 0).toLocaleString();
            const bl = activeRun.broken_links ?? 0;
            const blEl = document.getElementById('lcStatBroken');
            blEl.textContent = bl;
            blEl.style.color = bl > 0 ? 'var(--red)' : 'var(--green)';
            document.getElementById('lcSummary').style.display = '';
        }

        function lcHideProgress() {
            document.getElementById('lcProgress').style.display = 'none';
            document.getElementById('lcRunBtn').style.display = '';
            document.getElementById('lcCancelBtn').style.display = 'none';
            document.getElementById('lcStatus').textContent = '';
        }

        // ── Polling ───────────────────────────────────────────────────────────────
        function startLcPoll() {
            if (_lcPollTimer) return;
            _lcPollTimer = setInterval(async () => {
                try {
                    const status = await lcGet('/api/linkcheck/status');
                    if (status.active_run) {
                        lcShowProgress(status.active_run);
                    } else {
                        clearInterval(_lcPollTimer);
                        _lcPollTimer = null;
                        lcHideProgress();
                        document.getElementById('lcRunBtn').disabled = false;
                        if (_lcViewMode === 'sites') loadLcSiteStatus();
                        else { await loadLcRuns(); loadLcRunResults(null); }
                    }
                } catch { /* network hiccup */ }
            }, 2000);
        }

        // ── Run button ────────────────────────────────────────────────────────────
        document.getElementById('lcRunBtn').addEventListener('click', async () => {
            if (_lcSiteList.length && _lcSelectedIds.size === 0) {
                document.getElementById('lcStatus').textContent =
                    'No sites selected — click "Options" to choose which sites to check.';
                return;
            }
            document.getElementById('lcRunBtn').disabled = true;
            document.getElementById('lcStatus').textContent = 'Starting…';

            const body = {
                check_internal: _lcCheckInternal,
                check_external: _lcCheckExternal,
            };
            if (_lcSiteList.length && _lcSelectedIds.size < _lcSiteList.length) {
                body.site_ids = [..._lcSelectedIds];
            }
            try {
                const result = await lcPost('/api/linkcheck/run', body);
                if (result.error) {
                    document.getElementById('lcStatus').textContent = `Error: ${result.error}`;
                    document.getElementById('lcRunBtn').disabled = false;
                    return;
                }
                document.getElementById('lcStatus').textContent = '';
                lcShowProgress({ total_sites: result.total_sites, checked_sites: 0, broken_links: 0 });
                startLcPoll();
            } catch (e) {
                document.getElementById('lcStatus').textContent = `Error: ${e.message}`;
                document.getElementById('lcRunBtn').disabled = false;
            }
        });

        // ── Cancel button ─────────────────────────────────────────────────────────
        document.getElementById('lcCancelBtn').addEventListener('click', async () => {
            document.getElementById('lcCancelBtn').disabled = true;
            document.getElementById('lcCancelBtn').textContent = 'Cancelling…';
            try {
                await lcPost('/api/linkcheck/cancel');
            } catch {
                document.getElementById('lcCancelBtn').disabled = false;
                document.getElementById('lcCancelBtn').textContent = '✕ Cancel';
            }
        });

        // ── Site selector ─────────────────────────────────────────────────────────
        async function loadLcSiteList() {
            try {
                const resp = await fetch('/api/sites/registry');
                if (!resp.ok) return;
                const all = await resp.json();
                _lcSiteList = all.filter(s => !s.is_removed);
            } catch { return; }

            // Restore saved selection from localStorage
            try {
                const saved = JSON.parse(localStorage.getItem(LC_STORAGE_KEY) || 'null');
                if (Array.isArray(saved) && saved.length) {
                    const valid = new Set(_lcSiteList.map(s => String(s.id)));
                    _lcSelectedIds = new Set(saved.filter(id => valid.has(String(id))).map(String));
                    if (_lcSelectedIds.size === 0) _lcSelectedIds = new Set(_lcSiteList.map(s => String(s.id)));
                } else {
                    _lcSelectedIds = new Set(_lcSiteList.map(s => String(s.id)));
                }
            } catch {
                _lcSelectedIds = new Set(_lcSiteList.map(s => String(s.id)));
            }
            updateLcSiteCountBadge();
        }

        function saveLcSelectedSites() {
            localStorage.setItem(LC_STORAGE_KEY, JSON.stringify([..._lcSelectedIds]));
        }

        function updateLcSiteCountBadge() {
            const total = _lcSiteList.length;
            const sel   = _lcSelectedIds.size;
            document.getElementById('lcSiteCount').textContent =
                sel === total ? `(${total})` : `(${sel}/${total})`;
            const modalCount = document.getElementById('lcSiteSelectCount');
            if (modalCount) modalCount.textContent = `${sel} of ${total} selected`;
        }

        function renderLcSiteSelectList() {
            const listEl = document.getElementById('lcSiteSelectList');
            listEl.innerHTML = _lcSiteList.map(s => {
                const checked = _lcSelectedIds.has(String(s.id)) ? 'checked' : '';
                const domain = (s.url || '').replace(/^https?:\/\//, '');
                return `<label style="display:flex; align-items:center; gap:10px; padding:7px 18px; cursor:pointer; font-size:13px;" class="lc-site-select-row">
                    <input type="checkbox" data-site-id="${s.id}" ${checked}
                           style="accent-color:var(--accent); width:15px; height:15px; cursor:pointer; flex-shrink:0;">
                    <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(s.name)}</span>
                    <span style="font-size:11px; color:var(--text-muted); flex-shrink:0;">${escapeHtml(domain)}</span>
                </label>`;
            }).join('');
            listEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', () => {
                    const id = String(cb.dataset.siteId);
                    if (cb.checked) _lcSelectedIds.add(id);
                    else _lcSelectedIds.delete(id);
                    syncLcSelectAll();
                    updateLcSiteCountBadge();
                    saveLcSelectedSites();
                });
            });
            syncLcSelectAll();
            updateLcSiteCountBadge();
        }

        function syncLcSelectAll() {
            const allCb = document.getElementById('lcSelectAll');
            allCb.checked = _lcSelectedIds.size === _lcSiteList.length;
            allCb.indeterminate = _lcSelectedIds.size > 0 && _lcSelectedIds.size < _lcSiteList.length;
        }

        document.getElementById('lcSelectAll').addEventListener('change', e => {
            _lcSelectedIds = e.target.checked
                ? new Set(_lcSiteList.map(s => String(s.id)))
                : new Set();
            document.querySelectorAll('#lcSiteSelectList input[type="checkbox"]').forEach(cb => {
                cb.checked = e.target.checked;
            });
            updateLcSiteCountBadge();
            saveLcSelectedSites();
        });

        function closeLcSiteSelectModal() {
            // Reset Set Default button if it was mid-confirm
            const setBtn = document.getElementById('lcSetDefaultBtn');
            if (setBtn.dataset.state === 'confirm') {
                clearTimeout(_lcSetDefaultTimer);
                setBtn.dataset.state   = 'idle';
                setBtn.textContent     = 'Set Default';
                setBtn.style.background   = '';
                setBtn.style.borderColor  = '';
                setBtn.style.color        = '';
            }
            document.getElementById('lcSiteSelectOverlay').style.display = 'none';
            modalRemove('lcSiteSelectOverlay');
        }

        // ── Scope toggles ─────────────────────────────────────────────────────────
        function syncLcScopeButtons() {
            const intBtn = document.getElementById('lcScopeInternal');
            const extBtn = document.getElementById('lcScopeExternal');
            const note   = document.getElementById('lcScopeNote');
            intBtn.classList.toggle('active', _lcCheckInternal);
            intBtn.textContent = _lcCheckInternal ? '✓ Internal' : 'Internal';
            extBtn.classList.toggle('active', _lcCheckExternal);
            extBtn.textContent = _lcCheckExternal ? '✓ External' : 'External';
            if (_lcCheckInternal && _lcCheckExternal) {
                note.textContent = 'Checking both internal and external links.';
            } else if (_lcCheckExternal) {
                note.textContent = 'Checking external links only.';
            } else {
                note.textContent = 'Checking internal links only. External links are classified and counted but not checked.';
            }
        }

        function saveLcScope() {
            localStorage.setItem(LC_SCOPE_KEY,
                JSON.stringify({ internal: _lcCheckInternal, external: _lcCheckExternal }));
        }

        document.getElementById('lcScopeInternal').addEventListener('click', () => {
            _lcCheckInternal = !_lcCheckInternal;
            syncLcScopeButtons();
            saveLcScope();
        });
        document.getElementById('lcScopeExternal').addEventListener('click', () => {
            _lcCheckExternal = !_lcCheckExternal;
            syncLcScopeButtons();
            saveLcScope();
        });

        document.getElementById('lcSiteSelectBtn').addEventListener('click', () => {
            renderLcSiteSelectList();
            document.getElementById('lcSiteSelectOverlay').style.display = 'flex';
            modalPush('lcSiteSelectOverlay', closeLcSiteSelectModal);
        });
        document.getElementById('lcSiteSelectCloseBtn').addEventListener('click', closeLcSiteSelectModal);
        document.getElementById('lcSiteSelectDoneBtn').addEventListener('click', closeLcSiteSelectModal);
        document.getElementById('lcSiteSelectOverlay').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeLcSiteSelectModal();
        });

        // ── Default options (Restore / Set) ───────────────────────────────────────

        /** Apply saved or factory defaults to scope toggles + site selection. */
        function restoreLcDefaults() {
            let defaults = null;
            try {
                defaults = JSON.parse(localStorage.getItem(LC_DEFAULTS_KEY) || 'null');
            } catch { /* ignore */ }

            if (defaults) {
                _lcCheckInternal = defaults.internal !== false;
                _lcCheckExternal = !!defaults.external;
                if (defaults.site_ids === null) {
                    // null means "all sites"
                    _lcSelectedIds = new Set(_lcSiteList.map(s => String(s.id)));
                } else if (Array.isArray(defaults.site_ids)) {
                    const valid = new Set(_lcSiteList.map(s => String(s.id)));
                    _lcSelectedIds = new Set(
                        defaults.site_ids.filter(id => valid.has(String(id))).map(String)
                    );
                    // If none of the saved sites exist any more, fall back to all
                    if (_lcSelectedIds.size === 0) {
                        _lcSelectedIds = new Set(_lcSiteList.map(s => String(s.id)));
                    }
                }
            } else {
                // Factory defaults
                _lcCheckInternal = true;
                _lcCheckExternal = false;
                _lcSelectedIds   = new Set(_lcSiteList.map(s => String(s.id)));
            }

            // Persist the restored values as the current selection
            saveLcScope();
            saveLcSelectedSites();

            // Refresh UI
            syncLcScopeButtons();
            renderLcSiteSelectList(); // redraws checkboxes
            updateLcSiteCountBadge();
        }

        /** Save the current scope + site selection as the new default. */
        function lcSaveAsDefault() {
            localStorage.setItem(LC_DEFAULTS_KEY, JSON.stringify({
                internal:  _lcCheckInternal,
                external:  _lcCheckExternal,
                // null = all sites (more resilient to sites being added later)
                site_ids:  _lcSelectedIds.size === _lcSiteList.length
                    ? null
                    : [..._lcSelectedIds],
            }));
        }

        document.getElementById('lcRestoreDefaultBtn').addEventListener('click', () => {
            restoreLcDefaults();
            // Brief visual confirmation
            const btn = document.getElementById('lcRestoreDefaultBtn');
            btn.textContent = 'Restored ✓';
            btn.style.color = 'var(--green)';
            setTimeout(() => {
                btn.textContent = 'Restore Default';
                btn.style.color = '';
            }, 1800);
        });

        document.getElementById('lcSetDefaultBtn').addEventListener('click', () => {
            const btn = document.getElementById('lcSetDefaultBtn');
            if (btn.dataset.state === 'confirm') {
                // Second click — confirmed
                clearTimeout(_lcSetDefaultTimer);
                lcSaveAsDefault();
                btn.dataset.state   = 'idle';
                btn.textContent     = 'Saved ✓';
                btn.style.background   = '';
                btn.style.borderColor  = '';
                btn.style.color        = 'var(--green)';
                _lcSetDefaultTimer = setTimeout(() => {
                    btn.textContent  = 'Set Default';
                    btn.style.color  = '';
                }, 1800);
            } else {
                // First click — enter confirm state (auto-cancels after 3 s)
                btn.dataset.state   = 'confirm';
                btn.textContent     = 'Confirm?';
                btn.style.background   = 'var(--accent)';
                btn.style.borderColor  = 'var(--accent)';
                btn.style.color        = '#fff';
                _lcSetDefaultTimer = setTimeout(() => {
                    btn.dataset.state  = 'idle';
                    btn.textContent    = 'Set Default';
                    btn.style.background  = '';
                    btn.style.borderColor = '';
                    btn.style.color       = '';
                }, 3000);
            }
        });

        // downloadCsv moved to dom.js

        function exportLcSiteStatusCsv() {
            const today = new Date().toISOString().slice(0, 10);
            const rows = [['Site', 'Domain', 'Last Checked', 'Run ID', 'Pages Crawled', 'Total Links', 'Working', 'Broken']];
            const activeSites = _lcSiteList.filter(s => !s.is_removed);
            activeSites.forEach(site => {
                const st = _lcLastSiteStatusData[String(site.id)];
                const total    = st ? (st.links_checked ?? 0) : '';
                const broken   = st ? (st.broken_count  ?? 0) : '';
                const working  = (total !== '' && broken !== '') ? total - broken : '';
                const pages    = st ? (st.pages_crawled ?? 0) : '';
                const runId    = st ? (st.run_id ?? '') : '';
                const domain   = (site.url || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
                const lastChecked = st?.run_started_at
                    ? new Date(st.run_started_at + 'Z').toISOString().slice(0, 16).replace('T', ' ')
                    : '';
                rows.push([site.name || '', domain, lastChecked, runId, pages, total, working, broken]);
            });
            downloadCsv(rows, `link-check-sites-${today}.csv`);
        }

        function exportLcRunsCsv() {
            if (!_lcLastRunData) return;
            const run = _lcLastRunData;
            const sites = run.sites || [];
            const d = new Date((run.started_at || '') + 'Z');
            const dateStr = d.toISOString().slice(0, 10);
            const rows = [
                ['Run ID', 'Started', 'Finished', 'Status', 'Total Sites', 'Pages Crawled', 'Links Checked', 'Broken'],
                [run.id ?? '', run.started_at ?? '', run.finished_at ?? '', run.status ?? '',
                 run.total_sites ?? 0, run.total_pages_crawled ?? 0,
                 run.total_links_checked ?? 0, run.total_broken ?? 0],
                [],
                ['Site', 'Domain', 'Pages Crawled', 'Links Checked', 'Working', 'Broken',
                 '(Broken) Status/Error', '(Broken) Link URL', '(Broken) Found On'],
            ];
            sites.forEach(site => {
                const total  = site.links_checked ?? 0;
                const broken = site.broken_count  ?? 0;
                const domain = (site.site_url || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
                rows.push([site.site_name || '', domain,
                    site.pages_crawled ?? 0, total, total - broken, broken,
                    '', '', '']);
                (site.broken_links || []).forEach(link => {
                    rows.push(['', '', '', '', '', '',
                        link.status_code || link.error || '',
                        link.link_url    || '',
                        link.source_page || '']);
                });
            });
            downloadCsv(rows, `link-check-run-${run.id ?? 0}-${dateStr}.csv`);
        }

        async function exportLcSiteHistoryCsv() {
            const siteName = (_lcCurrentHistorySite.name || 'site')
                .replace(/[^a-z0-9]/gi, '-').toLowerCase();
            const siteId = _lcCurrentHistorySite.id;
            const today = new Date().toISOString().slice(0, 10);
            const rows = [['Date', 'Run ID', 'Pages Crawled', 'Total Links', 'Working', 'Broken', 'Status/Error', 'Link URL', 'Found On']];
            for (const r of _lcLastHistoryData) {
                const d = new Date((r.started_at || '') + 'Z');
                const total  = r.links_checked ?? 0;
                const broken = r.broken_count  ?? 0;
                rows.push([
                    d.toISOString().slice(0, 16).replace('T', ' '),
                    r.run_id ?? '',
                    r.pages_crawled ?? 0,
                    total, total - broken, broken,
                    '', '', '',
                ]);
                if (broken > 0) {
                    try {
                        const links = await lcGet(`/api/linkcheck/results/${r.run_id}/site/${siteId}`);
                        links.forEach(link => rows.push([
                            '', '', '', '', '', '',
                            link.status_code || link.error || '',
                            link.link_url    || '',
                            link.source_page || '',
                        ]));
                    } catch { /* skip broken-link detail if fetch fails */ }
                }
            }
            downloadCsv(rows, `link-check-history-${siteName}-${today}.csv`);
        }

        document.getElementById('lcExportBtn').addEventListener('click', () => {
            const historyPanel = document.getElementById('lcSiteHistoryPanel');
            if (historyPanel.style.display !== 'none') {
                exportLcSiteHistoryCsv();
            } else if (_lcViewMode === 'runs') {
                exportLcRunsCsv();
            } else {
                exportLcSiteStatusCsv();
            }
        });

        // ============================================================================

        // ============================================================================
        // Link Checker sub-view route handler
        // ============================================================================
        // Moved here (from app.js's former "Sub-view route handlers" section) now
        // that all the state and functions it touches (_lcViewMode, _lcSelectedRunId,
        // _lcSiteList, _lcCurrentHistorySite, loadLinkChecker, openLcSiteHistory,
        // closeLcSiteHistory) live in this module.
        let _pendingLcSiteHistory = null; // site_id — set when routing to linkchecker/sites/{id} before data loads

        function _applyLcSubview(subview, id) {
            if (subview === 'runs') {
                _lcViewMode = 'runs';
                if (id) _lcSelectedRunId = String(id);
                document.getElementById('lcViewSites').className = 'btn';
                document.getElementById('lcViewRuns').className  = 'btn btn-primary';
                document.getElementById('lcRunSelect').style.display = '';
                loadLinkChecker();
                return;
            }
            // subview === 'sites' (possibly with a site-id for drill-down)
            _lcViewMode = 'sites';
            document.getElementById('lcViewSites').className = 'btn btn-primary';
            document.getElementById('lcViewRuns').className  = 'btn';
            document.getElementById('lcRunSelect').style.display = 'none';
            if (id) {
                const sid = String(id);
                const site = _lcSiteList.find(s => String(s.id) === sid);
                if (site && _lcSiteList.length) {
                    // Site list already loaded — open drill-down directly (no redundant reload)
                    openLcSiteHistory(sid, site.name || '', site.url || '');
                } else {
                    // Load link checker first; pending flag applied after render
                    _pendingLcSiteHistory = sid;
                    if (_lcCurrentHistorySite.id) closeLcSiteHistory();
                    loadLinkChecker();
                }
            } else {
                if (_lcCurrentHistorySite.id) closeLcSiteHistory();
                loadLinkChecker();
            }
        }

        registerRoute('linkchecker', (subview, id) => _applyLcSubview(subview, id));

        // Setter for the Site Dashboard's Updates-style Link Check panel (app.js's
        // loadSdLinkCheck), which needs to snapshot data into this module's private
        // _lcLastHistoryData for CSV export / re-render — imported bindings are
        // read-only, so a plain assignment from app.js isn't possible.
        export function setLcLastHistoryData(data) {
            _lcLastHistoryData = data;
        }
