// ============================================================================
// Sites Tab — Site List, Site Detail, Site Dashboard
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { escapeHtml, showStatus } from './dom.js';
import { buildHash, _saveLastSubview, registerRoute } from './router.js';
import { normalizeRecord, renderUpdatesRowsInto } from './history.js';
import { renderRegSiteHistoryInto } from './regression.js';
import { renderLcSiteHistoryBody, setLcLastHistoryData } from './linkcheck.js';

import { dbg } from './debug.js';
dbg('module', 'sites.js loaded');

        // Sites Tab
        // ============================================================================
        let _sitesList = [];
        let _siteDetailId = null;

        async function loadSitesTab() {
            const status = document.getElementById('sitesStatus');
            const empty  = document.getElementById('sitesEmpty');
            const table  = document.getElementById('sitesTable');
            status.textContent = 'Loading…';
            try {
                const resp = await fetch('/api/sites');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                _sitesList = await resp.json();
                status.textContent = '';
                if (!_sitesList.length) {
                    empty.style.display = '';
                    table.style.display = 'none';
                } else {
                    empty.style.display = 'none';
                    table.style.display = '';
                    renderSitesTable();
                }
                // Apply any sub-view route that was deferred until sites loaded
                if (_pendingSiteRoute) {
                    const { subview, id } = _pendingSiteRoute;
                    _pendingSiteRoute = null;
                    if (subview === 'dashboard' && id) openSiteDashboard(id);
                    else if (subview === 'detail' && id) openSiteDetail(id);
                }
            } catch (e) {
                status.textContent = `Error loading sites: ${e.message}`;
            }
        }

        function renderSitesTable() {
            const tbody = document.getElementById('sitesBody');
            const active  = _sitesList.filter(s => !s.is_removed);
            const removed = _sitesList.filter(s => s.is_removed);

            const renderRow = s => {
                let pages = [];
                try { pages = JSON.parse(s.test_pages || '[]'); } catch {}
                const pageCount = pages.length || 1;
                const clientHtml = s.client_name
                    ? `<span style="color:var(--text);">${escapeHtml(s.client_name)}</span>`
                    : `<span style="color:var(--text-muted);">—</span>`;
                const notesHtml = s.notes
                    ? `<span style="color:var(--text-muted); font-size:12px;" title="${escapeHtml(s.notes)}">${escapeHtml(s.notes.slice(0, 40))}${s.notes.length > 40 ? '…' : ''}</span>`
                    : `<span style="color:var(--text-muted);">—</span>`;
                const removedBadge = s.is_removed
                    ? `<span style="display:inline-block; font-size:10px; font-weight:600; padding:1px 6px; border-radius:3px; background:var(--yellow-bg); color:var(--yellow); margin-left:6px; vertical-align:middle;">removed</span>`
                    : '';
                const rowOpacity = s.is_removed ? 'opacity:0.6;' : '';
                return `<tr style="cursor:pointer; ${rowOpacity}" data-site-id="${s.id}">
                    <td>
                        <div style="font-weight:500;">${escapeHtml(s.name)}${removedBadge}</div>
                        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml((s.url || '').replace(/^https?:\/\//, ''))}</div>
                    </td>
                    <td>${clientHtml}</td>
                    <td style="text-align:center;">
                        <span style="color:${pageCount > 1 ? 'var(--accent)' : 'var(--text-muted)'}; font-weight:${pageCount > 1 ? '600' : '400'};">${pageCount}</span>
                    </td>
                    <td>${notesHtml}</td>
                    <td style="text-align:right; white-space:nowrap;">
                        <button class="btn site-hb-btn" data-site-id="${s.id}" style="font-size:12px; padding:5px 10px;" title="Run Heartbeat scan for this site">♥</button>
                        <button class="btn site-edit-btn" data-site-id="${s.id}" style="font-size:12px; padding:5px 10px; margin-left:4px;">Edit</button>
                    </td>
                </tr>`;
            };

            let html = active.map(renderRow).join('');
            if (removed.length) {
                html += `<tr><td colspan="5" style="padding:12px 0 4px; font-size:11px; font-weight:600; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; border-top:1px solid var(--border); pointer-events:none;">
                    Removed from MainWP (${removed.length})
                </td></tr>`;
                html += removed.map(renderRow).join('');
            }
            tbody.innerHTML = html;

            tbody.querySelectorAll('tr[data-site-id]').forEach(row => {
                row.addEventListener('click', (e) => {
                    if (e.target.closest('.site-edit-btn') || e.target.closest('.site-hb-btn')) return;
                    openSiteDashboard(row.dataset.siteId);
                });
            });
            tbody.querySelectorAll('.site-edit-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openSiteDetail(btn.dataset.siteId);
                });
            });
            tbody.querySelectorAll('.site-hb-btn').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const siteId = btn.dataset.siteId;
                    btn.disabled = true;
                    btn.textContent = '…';
                    try {
                        const resp = await fetch('/api/heartbeat/run', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ site_ids: [parseInt(siteId)] }),
                        });
                        const data = await resp.json();
                        if (data.error) { alert(data.error); }
                        else { openSiteDashboard(siteId); }
                    } catch(err) { alert('Heartbeat start failed: ' + err.message); }
                    btn.disabled = false;
                    btn.textContent = '♥';
                });
            });
        }

        // ── Site detail page ─────────────────────────────────────────────────────────
        function openSiteDetail(siteId) {
            const site = _sitesList.find(s => String(s.id) === String(siteId));
            if (!site) return;
            _siteDetailId = siteId;
            history.replaceState(null, '', buildHash({ tab: 'sites', subview: 'detail', id: siteId }));
            _saveLastSubview('sites', 'detail');

            document.getElementById('siteDetailName').textContent = site.name;
            document.getElementById('siteDetailUrl').textContent = site.url || '';
            document.getElementById('siteConfigClient').value = site.client_name || '';
            document.getElementById('siteConfigNotes').value = site.notes || '';
            document.getElementById('siteConfigDiffThreshold').value =
                site.diff_threshold != null ? site.diff_threshold : 1.0;
            document.getElementById('siteDetailStatus').style.display = 'none';

            // Removed-from-MainWP banner
            const banner = document.getElementById('siteRemovedBanner');
            if (site.is_removed) {
                const dateEl = document.getElementById('siteRemovedDate');
                if (site.removed_from_mainwp_at) {
                    const d = new Date(site.removed_from_mainwp_at + 'Z');
                    dateEl.textContent = `(since ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })})`;
                } else {
                    dateEl.textContent = '';
                }
                banner.style.display = '';
            } else {
                banner.style.display = 'none';
            }

            let pages = [];
            try { pages = JSON.parse(site.test_pages || '[]'); } catch {}
            if (!pages.length) pages = [site.url || ''];
            renderPagesList(pages);

            document.getElementById('sitesListView').style.display = 'none';
            document.getElementById('siteDetail').style.display = '';
            document.getElementById('siteConfigClient').focus();
        }

        function closeSiteDetail() {
            history.replaceState(null, '', buildHash({ tab: 'sites' }));
            document.getElementById('siteDetail').style.display = 'none';
            document.getElementById('sitesListView').style.display = '';
            _siteDetailId = null;
        }

        // ── Site Dashboard ───────────────────────────────────────────────────────────
        export let _sdSiteId = null;
        let _sdHbPollTimer = null;

        async function openSiteDashboard(siteId) {
            const site = _sitesList.find(s => String(s.id) === String(siteId));
            if (!site) return;
            _sdSiteId = String(siteId);
            history.replaceState(null, '', buildHash({ tab: 'sites', subview: 'dashboard', id: siteId }));
            _saveLastSubview('sites', 'dashboard');

            document.getElementById('sdName').textContent = site.name;
            const metaParts = [site.url || ''];
            if (site.client_name) metaParts.push(site.client_name);
            document.getElementById('sdMeta').textContent = metaParts.join(' · ');

            document.getElementById('sitesListView').style.display = 'none';
            document.getElementById('siteDetail').style.display = 'none';
            document.getElementById('siteDashboard').style.display = '';

            document.getElementById('sdObContent').innerHTML =
                '<div class="sd-section-empty">Loading…</div>';
            document.getElementById('sdHbContent').innerHTML =
                '<div class="sd-section-empty">Loading…</div>';
            document.getElementById('sdRegressionContent').innerHTML =
                '<div class="sd-section-empty">Loading…</div>';
            document.getElementById('sdLinkCheckContent').innerHTML =
                '<div class="sd-section-empty">Loading…</div>';
            document.getElementById('sdUpdateContent').innerHTML =
                '<div class="sd-section-empty">Loading…</div>';

            // Reset show-hidden and edit toggles
            document.getElementById('sdObContent').classList.remove('sd-ob-show-hidden');
            document.getElementById('sdObShowHiddenBtn').classList.remove('active');
            document.getElementById('sdObShowHiddenBtn').textContent = 'Show Hidden';
            document.getElementById('sdObEditCheck').checked = false;
            document.getElementById('sdObContent').classList.add('sd-ob-locked');

            await Promise.all([
                _sdOnboardingLoader ? _sdOnboardingLoader() : Promise.resolve(),
                loadSdHeartbeat(),
                loadSdRegression(),
                loadSdLinkCheck(),
                loadSdUpdates(),
            ]);
        }

        // The Onboarding section of the Site Dashboard (loadSdOnboarding) still
        // lives in app.js — it hasn't been peeled into its own module yet. sites.js
        // can't import it directly (app.js is the entry point that imports sites.js,
        // not the other way around), so app.js registers its loader here instead.
        let _sdOnboardingLoader = null;
        export function registerSdOnboardingLoader(fn) {
            _sdOnboardingLoader = fn;
        }

        function closeSiteDashboard() {
            _sdSiteId = null;
            clearTimeout(_sdHbPollTimer);
            history.replaceState(null, '', buildHash({ tab: 'sites' }));
            document.getElementById('siteDashboard').style.display = 'none';
            document.getElementById('sitesListView').style.display = '';
        }

        document.getElementById('sdBackBtn').addEventListener('click', closeSiteDashboard);

        document.getElementById('sdEditBtn').addEventListener('click', () => {
            if (!_sdSiteId) return;
            document.getElementById('siteDashboard').style.display = 'none';
            openSiteDetail(_sdSiteId);
        });

        document.getElementById('sdHbRescanBtn').addEventListener('click', async () => {
            if (!_sdSiteId) return;
            const btn = document.getElementById('sdHbRescanBtn');
            btn.disabled = true;
            btn.textContent = 'Starting…';
            try {
                const resp = await fetch('/api/heartbeat/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ site_ids: [parseInt(_sdSiteId)] }),
                });
                const data = await resp.json();
                if (data.error && resp.status !== 409) {
                    alert(data.error);
                } else {
                    sdHbStartPolling();
                }
            } catch (e) { alert('Heartbeat start failed: ' + e.message); }
            btn.disabled = false;
            btn.textContent = 'Rescan';
        });

        document.getElementById('sdHbCancelBtn').addEventListener('click', async () => {
            await fetch('/api/heartbeat/cancel', { method: 'POST' });
        });

        function sdHbStartPolling() {
            clearTimeout(_sdHbPollTimer);
            document.getElementById('sdHbCancelBtn').style.display = '';
            document.getElementById('sdHbRescanBtn').disabled = true;
            document.getElementById('sdHbLastScan').textContent = '(scanning…)';
            _sdHbPollTimer = setTimeout(sdHbPollTick, 2000);
        }

        async function sdHbPollTick() {
            if (!_sdSiteId) return;
            try {
                const resp = await fetch('/api/heartbeat/status');
                const data = await resp.json();
                if (data.running) {
                    _sdHbPollTimer = setTimeout(sdHbPollTick, 2000);
                } else {
                    document.getElementById('sdHbCancelBtn').style.display = 'none';
                    document.getElementById('sdHbRescanBtn').disabled = false;
                    await loadSdHeartbeat();
                }
            } catch { _sdHbPollTimer = setTimeout(sdHbPollTick, 3000); }
        }

        function hbStatusIcon(status) {
            if (status === 'pass') return '<span class="hb-pass">✓</span>';
            if (status === 'fail') return '<span class="hb-fail">✗</span>';
            return '<span class="hb-unknown">—</span>';
        }

        function hbRobotsHtml(status, version) {
            if (status === 'bba') return `<span class="hb-bba">Blue Blaze ${version || ''}</span>`;
            if (status === 'custom') return '<span class="hb-custom">Custom</span>';
            if (status === 'none') return '<span class="hb-none">None</span>';
            return '<span class="hb-unknown">—</span>';
        }

        function hbExpiryHtml(expiresAt) {
            if (!expiresAt) return '<span class="hb-unknown">—</span>';
            const days = Math.round((new Date(expiresAt) - new Date()) / 86400000);
            const cls = days < 30 ? 'hb-expiry-danger' : days < 90 ? 'hb-expiry-warn' : 'hb-pass';
            return `<span class="${cls}">${expiresAt} (${days}d)</span>`;
        }

        async function loadSdHeartbeat() {
            if (!_sdSiteId) return;
            const el = document.getElementById('sdHbContent');
            const lastEl = document.getElementById('sdHbLastScan');
            try {
                const resp = await fetch(`/api/heartbeat/site/${_sdSiteId}/latest`);
                const r = await resp.json();
                if (!r || !r.checked_at) {
                    el.innerHTML = '<div class="sd-section-empty">No scan data yet — click Rescan.</div>';
                    lastEl.textContent = '';
                    return;
                }
                const ts = new Date(r.checked_at);
                lastEl.textContent = `Last: ${ts.toLocaleDateString()} ${ts.toLocaleTimeString()}`;

                const dnsRecords = (() => { try { return JSON.parse(r.dns_json || '{}'); } catch { return {}; } })();
                const rdapFlags = (() => { try { return JSON.parse(r.rdap_status_flags || '[]'); } catch { return []; } })();
                const rdapNs    = (() => { try { return JSON.parse(r.rdap_nameservers  || '[]'); } catch { return []; } })();
                const isLocked = rdapFlags.some(f => f.toLowerCase().includes('transfer'));
                const hasRdapData = r.rdap_registrar || r.rdap_expires_at || rdapFlags.length || rdapNs.length;
                const _eppLabel = f => ({ clientTransferProhibited:'Transfer locked', clientDeleteProhibited:'Deletion locked',
                    clientUpdateProhibited:'Update locked', serverTransferProhibited:'Server transfer lock',
                    serverDeleteProhibited:'Server deletion lock', ok:'Active' }[f] || f.replace(/([A-Z])/g,' $1').trim());
                const rdapPanelHtml = [
                    r.rdap_registrar  ? `<div><strong>Registrar:</strong> ${escapeHtml(r.rdap_registrar)}</div>` : '',
                    r.rdap_created_at ? `<div><strong>Registered:</strong> ${escapeHtml(r.rdap_created_at)}</div>` : '',
                    r.rdap_expires_at ? `<div><strong>Expires:</strong> ${escapeHtml(r.rdap_expires_at)}</div>` : '',
                    rdapFlags.length  ? `<div><strong>Status:</strong> ${rdapFlags.map(f => escapeHtml(_eppLabel(f))).join(', ')}</div>` : '',
                    rdapNs.length     ? `<div><strong>Nameservers:</strong> ${rdapNs.map(escapeHtml).join(', ')}</div>` : '',
                    !r.rdap_registrar && !r.rdap_expires_at && !rdapNs.length
                        ? `<div style="color:var(--text-muted);">Registrar data not available via RDAP for this domain.</div>` : '',
                ].join('');

                el.innerHTML = `
                <div class="hb-grid">
                    <div class="hb-item"><span class="hb-label">SPF:</span> ${hbStatusIcon(r.spf_status)}</div>
                    <div class="hb-item"><span class="hb-label">DKIM:</span> ${hbStatusIcon(r.dkim_status)}${r.dkim_selector ? ` <span style="font-size:11px;color:var(--text-muted);">(${r.dkim_selector})</span>` : ''}</div>
                    <div class="hb-item"><span class="hb-label">DMARC:</span> ${hbStatusIcon(r.dmarc_status)}</div>
                    <div class="hb-item"><span class="hb-label">SMTP:</span> ${hbStatusIcon(r.smtp_status)}</div>
                    <div class="hb-item"><span class="hb-label">robots.txt:</span> ${hbRobotsHtml(r.robots_status, r.robots_version)}</div>
                    <div class="hb-item"><span class="hb-label">Sitemap:</span> ${hbStatusIcon(r.sitemap_status)}</div>
                    <div class="hb-item"><span class="hb-label">WP API:</span> ${hbStatusIcon(r.wp_api_status)}</div>
                    ${r.staging_status !== 'unknown' ? `<div class="hb-item"><span class="hb-label">Staging:</span> ${hbStatusIcon(r.staging_status)}</div>` : ''}
                    ${r.staging_auth_status !== 'unknown' ? `<div class="hb-item"><span class="hb-label">Basic Auth:</span> ${hbStatusIcon(r.staging_auth_status)}</div>` : ''}
                    ${r.rdap_registrar ? `<div class="hb-item"><span class="hb-label">Registrar:</span> <span>${escapeHtml(r.rdap_registrar)}</span></div>` : ''}
                    ${r.rdap_expires_at ? `<div class="hb-item"><span class="hb-label">Expires:</span> ${hbExpiryHtml(r.rdap_expires_at)}</div>` : ''}
                    ${r.rdap_expires_at ? `<div class="hb-item"><span class="hb-label">Locked:</span> ${isLocked ? '<span class="hb-pass">✓</span>' : '<span class="hb-fail">✗</span>'}</div>` : ''}
                </div>
                ${r.smtp_detail ? `<div style="font-size:11px;color:var(--text-muted);margin-top:8px;">SMTP: ${escapeHtml(r.smtp_detail)}</div>` : ''}
                <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;">
                    ${Object.keys(dnsRecords).length ? `<button class="hb-toggle-details" data-target="sdDnsDetails">Show DNS ▾</button>` : ''}
                    ${hasRdapData ? `<button class="hb-toggle-details" data-target="sdRdapDetails">Show WHOIS ▾</button>` : ''}
                    ${r.robots_content ? `<button class="hb-toggle-details" data-target="sdRobotsDetails">Show robots.txt ▾</button>` : ''}
                </div>
                <div id="sdDnsDetails" class="hb-details-panel">${escapeHtml(JSON.stringify(dnsRecords, null, 2))}</div>
                <div id="sdRdapDetails" class="hb-details-panel" style="font-size:12px; line-height:1.8;">${rdapPanelHtml}</div>
                <div id="sdRobotsDetails" class="hb-details-panel">${escapeHtml(r.robots_content || '')}</div>`;

                el.querySelectorAll('.hb-toggle-details').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const panel = document.getElementById(btn.dataset.target);
                        if (!panel) return;
                        const visible = panel.style.display === 'block';
                        panel.style.display = visible ? 'none' : 'block';
                        btn.textContent = btn.textContent.replace(visible ? '▴' : '▾', visible ? '▾' : '▴');
                    });
                });

                const active = await fetch('/api/heartbeat/status').then(r=>r.json());
                if (active.running) sdHbStartPolling();
            } catch(e) {
                el.innerHTML = `<div class="sd-section-empty">Error loading heartbeat data.</div>`;
            }
        }

        async function loadSdRegression() {
            if (!_sdSiteId) return;
            const el = document.getElementById('sdRegressionContent');
            el.innerHTML = '<div style="color:var(--text-muted);padding:16px;text-align:center;font-size:12px;">Loading…</div>';
            try {
                const rows = await fetch(`/api/regression/site/${_sdSiteId}/history`).then(r => r.json());
                el.innerHTML = `<div style="overflow-x:auto;">
                    <table class="log-table">
                        <thead><tr>
                            <th style="width:30px;">Status</th>
                            <th>Date</th>
                            <th>HTTP</th>
                            <th>Load</th>
                            <th>JS</th>
                            <th>Broken</th>
                            <th>Screenshot</th>
                            <th>Diff</th>
                        </tr></thead>
                        <tbody id="sdRegHistoryBody"></tbody>
                    </table>
                    <p id="sdRegHistoryEmpty" style="color:var(--text-muted);text-align:center;padding:16px;font-size:13px;display:none;">No regression runs for this site yet.</p>
                </div>`;
                renderRegSiteHistoryInto(
                    { tbody: document.getElementById('sdRegHistoryBody'), emptyEl: document.getElementById('sdRegHistoryEmpty'), idPrefix: 'sd-hist' },
                    rows
                );
            } catch { el.innerHTML = '<div class="sd-section-empty">Error loading regression history.</div>'; }
        }

        async function loadSdLinkCheck() {
            if (!_sdSiteId) return;
            const el = document.getElementById('sdLinkCheckContent');
            el.innerHTML = '<div style="color:var(--text-muted);padding:16px;text-align:center;font-size:12px;">Loading…</div>';
            try {
                const history = await fetch(`/api/linkcheck/site/${_sdSiteId}/history`).then(r => r.json());
                el.innerHTML = `<div style="overflow-x:auto;">
                    <table class="log-table">
                        <thead id="sdLcHistoryThead"></thead>
                        <tbody id="sdLcHistoryBody"></tbody>
                    </table>
                    <p id="sdLcHistoryEmpty" style="color:var(--text-muted);text-align:center;padding:16px;font-size:13px;display:none;">No link check runs for this site yet.</p>
                </div>`;
                setLcLastHistoryData(history);
                renderLcSiteHistoryBody(history, _sdSiteId,
                    { tbodyId: 'sdLcHistoryBody', theadId: 'sdLcHistoryThead', emptyId: 'sdLcHistoryEmpty' });
            } catch { el.innerHTML = '<div class="sd-section-empty">Error loading link check history.</div>'; }
        }

        let _sdUpdatesSort = { col: 'date', dir: 'desc' };

        async function loadSdUpdates() {
            if (!_sdSiteId) return;
            const el = document.getElementById('sdUpdateContent');
            el.innerHTML = '<div style="color:var(--text-muted);padding:16px;text-align:center;font-size:12px;">Loading…</div>';
            const cols = ['type', 'name', 'old_version', 'new_version', 'date'];
            const colLabels = { type: 'Type', name: 'Name', old_version: 'Old Version', new_version: 'New Version', date: 'Date' };
            try {
                const resp = await fetch(`/api/mainwp/update-history/cached?site_id=${_sdSiteId}&limit=50`);
                const raw = await resp.json();
                const records = (Array.isArray(raw) ? raw : (raw.records || [])).map(normalizeRecord);
                if (!records.length) {
                    el.innerHTML = '<div class="sd-section-empty">No update history for this site.</div>';
                    return;
                }
                el.innerHTML = `<div style="overflow-x:auto;">
                    <table class="log-table">
                        <thead id="sdUpdatesThead"></thead>
                        <tbody id="sdUpdatesBody"></tbody>
                    </table>
                    <p id="sdUpdatesEmpty" style="color:var(--text-muted);text-align:center;padding:16px;font-size:13px;display:none;">No records.</p>
                </div>`;
                function sdRenderUpdates() {
                    const thead = document.getElementById('sdUpdatesThead');
                    const tbody = document.getElementById('sdUpdatesBody');
                    thead.innerHTML = `<tr>${cols.map(col => {
                        const active = _sdUpdatesSort.col === col;
                        const arrow = active ? (_sdUpdatesSort.dir === 'asc' ? ' ↑' : ' ↓') : ' ⇅';
                        const bg = active ? 'background:var(--surface-2);' : '';
                        return `<th style="${bg}cursor:pointer;user-select:none;" data-col="${col}">${colLabels[col]}${arrow}</th>`;
                    }).join('')}</tr>`;
                    thead.querySelectorAll('th[data-col]').forEach(th => {
                        th.addEventListener('click', () => {
                            const col = th.dataset.col;
                            if (_sdUpdatesSort.col === col) {
                                _sdUpdatesSort.dir = _sdUpdatesSort.dir === 'asc' ? 'desc' : 'asc';
                            } else {
                                _sdUpdatesSort.col = col;
                                _sdUpdatesSort.dir = col === 'date' ? 'desc' : 'asc';
                            }
                            sdRenderUpdates();
                        });
                    });
                    renderUpdatesRowsInto({ tbody, records, columns: cols, sortState: _sdUpdatesSort });
                }
                sdRenderUpdates();
            } catch { el.innerHTML = '<div class="sd-section-empty">Error loading update history.</div>'; }
        }

        function renderPagesList(pages) {
            const list = document.getElementById('sitePagesList');
            list.innerHTML = pages.map((url, i) =>
                `<div class="page-row" data-idx="${i}">
                    <input type="url" class="field-input page-url-input"
                           value="${escapeHtml(url)}" placeholder="https://…">
                    <button class="page-remove-btn" data-idx="${i}" title="Remove">✕</button>
                </div>`
            ).join('');
            list.querySelectorAll('.page-remove-btn').forEach(btn => {
                btn.addEventListener('click', () => btn.closest('.page-row').remove());
            });
        }

        function getPagesFromDetail() {
            return [...document.querySelectorAll('.page-url-input')]
                .map(i => i.value.trim())
                .filter(Boolean);
        }

        document.getElementById('siteAddPageBtn').addEventListener('click', () => {
            const list = document.getElementById('sitePagesList');
            const idx = list.querySelectorAll('.page-row').length;
            const row = document.createElement('div');
            row.className = 'page-row';
            row.dataset.idx = idx;
            row.innerHTML = `<input type="url" class="field-input page-url-input" placeholder="https://…">
                <button class="page-remove-btn" title="Remove">✕</button>`;
            row.querySelector('.page-remove-btn').addEventListener('click', () => row.remove());
            list.appendChild(row);
            row.querySelector('input').focus();
        });

        document.getElementById('siteDetailBackBtn').addEventListener('click', closeSiteDetail);

        document.getElementById('siteDetailSaveBtn').addEventListener('click', async () => {
            if (!_siteDetailId) return;
            const saveBtn = document.getElementById('siteDetailSaveBtn');
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
            const payload = {
                client_name: document.getElementById('siteConfigClient').value.trim(),
                notes: document.getElementById('siteConfigNotes').value.trim(),
                test_pages: getPagesFromDetail(),
                diff_threshold: parseFloat(document.getElementById('siteConfigDiffThreshold').value) || 1.0,
            };
            try {
                const resp = await fetch(`/api/sites/config/${_siteDetailId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await resp.json();
                if (data.error) throw new Error(data.error);
                showStatus('siteDetailStatus', 'Saved successfully', 'success');
                await loadSitesTab();
            } catch (e) {
                showStatus('siteDetailStatus', `Save failed: ${e.message}`, 'error');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Changes';
            }
        });


        // ============================================================================
        // Sites sub-view route handler
        // ============================================================================
        // Moved here (from app.js's former "Sub-view route handlers" section) now
        // that all the state and functions it touches (_sitesList, _sdSiteId,
        // _sdHbPollTimer, loadSitesTab, openSiteDashboard, openSiteDetail) live in
        // this module.
        let _pendingSiteRoute = null; // {subview, id} — set when routing to sites before list loads

        function _applySitesRoute(subview, id) {
            if (!subview || subview === 'list') {
                clearTimeout(_sdHbPollTimer);
                _sdSiteId = null;
                document.getElementById('siteDashboard').style.display = 'none';
                document.getElementById('siteDetail').style.display = 'none';
                document.getElementById('sitesListView').style.display = '';
                if (!_sitesList.length) loadSitesTab();
            } else if ((subview === 'dashboard' || subview === 'detail') && id) {
                if (_sitesList.length) {
                    if (subview === 'dashboard') openSiteDashboard(id);
                    else openSiteDetail(id);
                } else {
                    _pendingSiteRoute = { subview, id };
                    document.getElementById('siteDashboard').style.display = 'none';
                    document.getElementById('siteDetail').style.display = 'none';
                    document.getElementById('sitesListView').style.display = '';
                    loadSitesTab(); // will apply _pendingSiteRoute after loading
                }
            } else {
                // Fallback — show list
                document.getElementById('siteDashboard').style.display = 'none';
                document.getElementById('siteDetail').style.display = 'none';
                document.getElementById('sitesListView').style.display = '';
                if (!_sitesList.length) loadSitesTab();
            }
        }

        registerRoute('sites', (subview, id) => _applySitesRoute(subview, id));
