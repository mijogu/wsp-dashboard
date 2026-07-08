// ============================================================================
// Regression Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { escapeHtml, safeJsonParse, modalPush, modalRemove, _sortTh } from './dom.js';
import { buildHash, navigate, _saveLastSubview, registerRoute } from './router.js';

import { dbg } from './debug.js';
dbg('module', 'regression.js loaded');

        // Regression Tab
        // ============================================================================
        let _regressionPollTimer = null;
        let _regViewMode = 'sites'; // 'sites' or 'runs'

        function setRegView(mode, runId) {
            _regViewMode = mode;
            document.getElementById('regViewSites').className = mode === 'sites' ? 'btn btn-primary' : 'btn';
            document.getElementById('regViewRuns').className = mode === 'runs' ? 'btn btn-primary' : 'btn';
            // Run-mode controls
            document.getElementById('regressionRunSelect').style.display = mode === 'runs' ? '' : 'none';
            document.getElementById('regressionDeleteBtn').style.display = 'none';
            document.getElementById('regressionRunInfo').textContent = '';
            if (mode === 'sites') {
                loadSiteStatus();
            } else if (runId) {
                loadRegressionResults(runId); // also calls loadRegressionRuns internally
            } else {
                loadRegressionRuns();
                loadRegressionResults();
            }
        }

        let _siteRegistryMap = {};  // site_id (string) → registry entry

        async function loadSiteStatus() {
            // Fetch registry in parallel so we can flag removed sites
            const [statusResp, registryResp] = await Promise.allSettled([
                fetch('/api/regression/site-status'),
                fetch('/api/sites/registry'),
            ]);

            if (registryResp.status === 'fulfilled' && registryResp.value.ok) {
                try {
                    const reg = await registryResp.value.json();
                    _siteRegistryMap = Object.fromEntries(reg.map(s => [String(s.id), s]));
                } catch { /* ignore */ }
            }

            try {
                if (!statusResp.value?.ok) {
                    document.getElementById('regressionBody').innerHTML = '';
                    document.getElementById('regressionEmpty').style.display = '';
                    document.getElementById('regressionSummary').style.display = 'none';
                    return;
                }
                const results = await statusResp.value.json();
                renderSiteStatusTable(results);
                if (_pendingRegSiteHistory) {
                    const sid = _pendingRegSiteHistory;
                    _pendingRegSiteHistory = null;
                    const site = _lastSiteStatusResults.find(r => String(r.site_id) === sid);
                    openSiteRegHistory(sid, site?.site_name || '', site?.page_url || site?.site_url || '');
                }
            } catch (e) {
                document.getElementById('regressionStatus').textContent = `Error: ${e.message}`;
            }
        }

        function renderSiteStatusTable(results) {
            if (results) _lastSiteStatusResults = results;
            const data = _lastSiteStatusResults;

            const tbody = document.getElementById('regressionBody');
            const empty = document.getElementById('regressionEmpty');
            const summary = document.getElementById('regressionSummary');

            if (!data.length) {
                tbody.innerHTML = '';
                empty.style.display = '';
                summary.style.display = 'none';
                return;
            }

            empty.style.display = 'none';
            summary.style.display = '';

            // Summary stats
            const total = data.length;
            const withIssues = data.filter(r => r.has_issues).length;
            const clean = total - withIssues;
            const loadTimes = data.filter(r => r.load_time_ms).map(r => r.load_time_ms);
            const avgLoad = loadTimes.length
                ? Math.round(loadTimes.reduce((a, b) => a + b, 0) / loadTimes.length)
                : null;

            document.getElementById('regStatTotal').textContent = total;
            document.getElementById('regStatClean').textContent = clean;
            document.getElementById('regStatIssues').textContent = withIssues;
            document.getElementById('regStatAvgLoad').textContent = avgLoad ? `${(avgLoad / 1000).toFixed(1)}s` : '—';

            // Sortable header
            const thead = document.querySelector('#regressionTable thead tr');
            thead.innerHTML =
                `<th style="width:40px;">Status</th>` +
                _sortTh('Site', 'site', _regSiteSort) +
                _sortTh('HTTP', 'http', _regSiteSort) +
                _sortTh('Load Time', 'load', _regSiteSort) +
                _sortTh('JS Errors', 'js', _regSiteSort) +
                _sortTh('Broken Resources', 'broken', _regSiteSort) +
                _sortTh('Last Checked', 'checked', _regSiteSort) +
                `<th style="width:80px;">Screenshot</th>` +
                _sortTh('Diff', 'diff', _regSiteSort, 'width:90px;');

            thead.querySelectorAll('[data-sort-col]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.sortCol;
                    if (_regSiteSort.col === col) _regSiteSort.dir *= -1;
                    else { _regSiteSort.col = col; _regSiteSort.dir = 1; }
                    renderSiteStatusTable();
                });
            });

            // Apply sort (default: issues first, then alpha)
            let sorted;
            if (_regSiteSort.col) {
                sorted = _sortRegData(data, _regSiteSort.col, _regSiteSort.dir);
            } else {
                sorted = [...data].sort((a, b) => {
                    if (a.has_issues !== b.has_issues) return b.has_issues - a.has_issues;
                    return (a.site_name || '').localeCompare(b.site_name || '');
                });
            }

            tbody.innerHTML = sorted.map((r, idx) => {
                const hasIssue = r.has_issues;
                const icon = hasIssue ? '⚠️' : '✅';
                const hasHttpError = r.http_status && r.http_status >= 400;
                const rowBg = hasHttpError ? 'background: var(--red-bg);'
                    : hasIssue ? 'background: var(--yellow-bg);' : '';

                const httpColor = !r.http_status ? 'var(--text-muted)'
                    : r.http_status < 300 ? 'var(--green)'
                    : r.http_status < 400 ? 'var(--yellow)'
                    : 'var(--red)';

                const lt = r.load_time_ms;
                const loadStr = lt ? `${(lt / 1000).toFixed(1)}s` : '—';
                const loadColor = !lt ? 'var(--text-muted)'
                    : lt > 10000 ? 'var(--red)'
                    : lt > 5000 ? 'var(--yellow)'
                    : 'var(--text)';

                const jsErrs = safeJsonParse(r.js_errors, []);
                const broken = safeJsonParse(r.broken_resources, []);

                const jsErrCell = jsErrs.length
                    ? `<span class="reg-detail-toggle" data-target="site-js-${idx}"
                          style="color:var(--red); font-size:12px; cursor:pointer; text-decoration:underline;"
                          >${jsErrs.length} error${jsErrs.length > 1 ? 's' : ''}</span>`
                    : '<span style="color:var(--text-muted); font-size:12px;">None</span>';

                const brokenCell = broken.length
                    ? `<span class="reg-detail-toggle" data-target="site-br-${idx}"
                          style="color:var(--red); font-size:12px; cursor:pointer; text-decoration:underline;"
                          >${broken.length} broken</span>`
                    : '<span style="color:var(--text-muted); font-size:12px;">None</span>';

                let diffCell;
                if (r.diff_score !== null && r.diff_score !== undefined) {
                    const score = parseFloat(r.diff_score);
                    const threshold = r.diff_threshold ?? 1.0;
                    const diffColor = score === 0 ? 'var(--green)'
                        : score < threshold ? 'var(--yellow)'
                        : 'var(--red)';
                    const hasCompare = r.diff_screenshot_path && r.prev_screenshot_path;
                    diffCell = `<span class="${hasCompare ? 'diff-score-link' : ''}"
                        style="color:${diffColor}; font-size:12px; font-weight:600; ${hasCompare ? 'cursor:pointer; text-decoration:underline;' : ''}"
                        data-prev="${r.prev_screenshot_path ? encodeURIComponent(r.prev_screenshot_path) : ''}"
                        data-diff="${r.diff_screenshot_path ? encodeURIComponent(r.diff_screenshot_path) : ''}"
                        data-current="${r.screenshot_path ? encodeURIComponent(r.screenshot_path) : ''}"
                        data-site="${escapeHtml(r.site_name)}"
                        data-score="${score}"
                        data-page-url="${encodeURIComponent(r.page_url || r.site_url || '')}"
                        >${score === 0 ? '0% — no change' : score.toFixed(2) + '%'}</span>`;
                } else {
                    diffCell = '<span style="color:var(--text-muted); font-size:12px;">—</span>';
                }

                let lastChecked = '—';
                const ts = r.run_started_at || r.checked_at;
                if (ts) {
                    const d = new Date(ts);
                    const ago = Date.now() - d.getTime();
                    if (ago < 3600000) lastChecked = `${Math.round(ago / 60000)}m ago`;
                    else if (ago < 86400000) lastChecked = `${Math.round(ago / 3600000)}h ago`;
                    else lastChecked = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                }

                const screenshotHtml = r.screenshot_path
                    ? `<a href="#" class="screenshot-link" data-src="/api/regression/screenshot/${encodeURIComponent(r.screenshot_path)}" data-site="${escapeHtml(r.site_name)}" style="color:var(--accent); font-size:12px;">View</a>`
                    : '<span style="color:var(--text-muted); font-size:12px;">—</span>';

                const jsDetailRow = jsErrs.length ? `
                    <tr id="site-js-${idx}" style="display:none; background:var(--surface-2);">
                        <td></td><td colspan="8" style="padding:6px 12px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">JS ERRORS</div>
                            ${jsErrs.map(e => `<div style="font-family:monospace; font-size:11px; color:var(--red); margin-bottom:2px;">${escapeHtml(String(e))}</div>`).join('')}
                        </td>
                    </tr>` : '';

                const brokenDetailRow = broken.length ? `
                    <tr id="site-br-${idx}" style="display:none; background:var(--surface-2);">
                        <td></td><td colspan="8" style="padding:6px 12px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">BROKEN RESOURCES</div>
                            ${broken.map(b => `<div style="font-family:monospace; font-size:11px; margin-bottom:2px;"><span style="color:var(--red); font-weight:500;">${b.status}</span> <span style="color:var(--text-muted);">${escapeHtml(b.url)}</span></div>`).join('')}
                        </td>
                    </tr>` : '';

                const regEntry = _siteRegistryMap[String(r.site_id)];
                const isRemoved = regEntry?.is_removed;
                const removedBadge = isRemoved
                    ? `<span style="display:inline-block; font-size:10px; font-weight:600; padding:1px 6px; border-radius:3px; background:var(--yellow-bg); color:var(--yellow); margin-left:6px; vertical-align:middle;">removed</span>`
                    : '';

                return `<tr style="${rowBg} cursor:pointer; ${isRemoved ? 'opacity:0.65;' : ''}"
                    data-site-id="${r.site_id}"
                    data-site-name="${escapeHtml(r.site_name || '')}"
                    data-site-url="${escapeHtml(r.page_url || r.site_url || '')}">
                    <td style="text-align:center;">${icon}</td>
                    <td>
                        <div style="font-weight:500;">${escapeHtml(r.site_name || '—')} <span style="color:var(--text-muted); font-size:11px;">›</span>${removedBadge}</div>
                        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml((r.page_url || r.site_url || '').replace(/^https?:\/\//, ''))}</div>
                        ${r.error ? `<div style="font-size:11px; color:var(--red); margin-top:2px;">${escapeHtml(r.error)}</div>` : ''}
                    </td>
                    <td style="color:${httpColor}; font-weight:500;">${r.http_status || '—'}</td>
                    <td style="color:${loadColor};">${loadStr}</td>
                    <td>${jsErrCell}</td>
                    <td>${brokenCell}</td>
                    <td style="font-size:12px; color:var(--text-muted);">${lastChecked}</td>
                    <td style="text-align:center;">${screenshotHtml}</td>
                    <td style="text-align:center;">${diffCell}</td>
                </tr>${jsDetailRow}${brokenDetailRow}`;
            }).join('');

            // Wire interactive elements
            tbody.querySelectorAll('.screenshot-link').forEach(link => {
                link.addEventListener('click', e => {
                    e.preventDefault();
                    e.stopPropagation();
                    document.getElementById('screenshotImg').src = link.dataset.src;
                    document.getElementById('screenshotTitle').textContent = link.dataset.site;
                    document.getElementById('screenshotOverlay').style.display = 'flex';
                    modalPush('screenshotOverlay', closeScreenshotOverlay);
                });
            });

            tbody.querySelectorAll('.reg-detail-toggle').forEach(el => {
                el.addEventListener('click', e => {
                    e.stopPropagation();
                    const target = document.getElementById(el.dataset.target);
                    if (target) {
                        const visible = target.style.display !== 'none';
                        target.style.display = visible ? 'none' : '';
                        el.style.fontWeight = visible ? 'normal' : '700';
                    }
                });
            });

            tbody.querySelectorAll('.diff-score-link').forEach(el => {
                el.addEventListener('click', e => {
                    e.stopPropagation();
                    const pageUrl = decodeURIComponent(el.dataset.pageUrl || '');
                    const prevPath = decodeURIComponent(el.dataset.prev || '');
                    const diffPath = decodeURIComponent(el.dataset.diff || '');
                    const currentPath = decodeURIComponent(el.dataset.current || '');
                    const score = el.dataset.score;
                    document.getElementById('diffTitle').textContent = `Visual Diff — ${el.dataset.site}`;
                    document.getElementById('diffSubtitle').textContent =
                        `${pageUrl || ''}  •  ${parseFloat(score).toFixed(2)}% pixels changed`;
                    document.getElementById('diffBaselineImg').src = prevPath ? `/api/regression/screenshot/${prevPath}` : '';
                    document.getElementById('diffDiffImg').src = diffPath ? `/api/regression/screenshot/${diffPath}` : '';
                    document.getElementById('diffCurrentImg').src = currentPath ? `/api/regression/screenshot/${currentPath}` : '';
                    document.getElementById('diffOverlay').style.display = 'flex';
                    modalPush('diffOverlay', closeDiffOverlay);
                });
            });

            // Row click → history drill-down (skip if an interactive element was clicked)
            tbody.querySelectorAll('tr[data-site-id]').forEach(row => {
                row.addEventListener('click', e => {
                    if (e.target.closest('a, button, .reg-detail-toggle, .diff-score-link')) return;
                    navigate({ tab: 'regression', subview: 'sites', id: row.dataset.siteId });
                });
            });
        }

        // ── Per-site regression history ──────────────────────────────────────────────
        let _currentHistorySiteId = null;

        function openSiteRegHistory(siteId, siteName, siteUrl) {
            _currentHistorySiteId = siteId;
            document.getElementById('siteHistoryTitle').textContent = siteName;
            document.getElementById('siteHistoryUrl').textContent = siteUrl || '';
            document.getElementById('regressionTableWrap').style.display = 'none';
            document.getElementById('regressionSummary').style.display = 'none';
            document.getElementById('siteHistoryPanel').style.display = '';
            loadSiteRegHistory(siteId);
        }

        function closeSiteRegHistory() {
            _currentHistorySiteId = null;
            history.replaceState(null, '', buildHash({ tab: 'regression', subview: 'sites' }));
            document.getElementById('siteHistoryPanel').style.display = 'none';
            document.getElementById('regressionTableWrap').style.display = '';
            document.getElementById('regressionSummary').style.display = '';
        }

        // Shared renderer — called by both the main regression panel and the Site Dashboard.
        // idPrefix must be unique per mount point to prevent detail-row id collisions.
        export function renderRegSiteHistoryInto({ tbody, emptyEl, idPrefix = 'hist' }, results) {
            if (!results.length) {
                tbody.innerHTML = '';
                if (emptyEl) emptyEl.style.display = '';
                return;
            }
            if (emptyEl) emptyEl.style.display = 'none';

            tbody.innerHTML = results.map((r, idx) => {
                const icon = r.has_issues ? '⚠️' : '✅';
                const hasHttpError = r.http_status && r.http_status >= 400;
                const jsErrs = safeJsonParse(r.js_errors, []);
                const broken = safeJsonParse(r.broken_resources, []);
                const rowBg = hasHttpError ? 'background:var(--red-bg);'
                    : r.has_issues ? 'background:var(--yellow-bg);' : '';

                const httpColor = !r.http_status ? 'var(--text-muted)'
                    : r.http_status < 300 ? 'var(--green)'
                    : r.http_status < 400 ? 'var(--yellow)'
                    : 'var(--red)';

                const lt = r.load_time_ms;
                const loadStr = lt ? `${(lt / 1000).toFixed(1)}s` : '—';
                const loadColor = !lt ? 'var(--text-muted)'
                    : lt > 10000 ? 'var(--red)'
                    : lt > 5000 ? 'var(--yellow)'
                    : 'var(--text)';

                const jsCell = jsErrs.length
                    ? `<span class="reg-detail-toggle" data-target="${idPrefix}-js-${idx}"
                          style="color:var(--red); font-size:12px; cursor:pointer; text-decoration:underline;"
                          >${jsErrs.length} error${jsErrs.length > 1 ? 's' : ''}</span>`
                    : '<span style="color:var(--text-muted); font-size:12px;">None</span>';

                const brokenCell = broken.length
                    ? `<span class="reg-detail-toggle" data-target="${idPrefix}-br-${idx}"
                          style="color:var(--red); font-size:12px; cursor:pointer; text-decoration:underline;"
                          >${broken.length} broken</span>`
                    : '<span style="color:var(--text-muted); font-size:12px;">None</span>';

                const screenshotHtml = r.screenshot_path
                    ? `<a href="#" class="screenshot-link" data-src="/api/regression/screenshot/${encodeURIComponent(r.screenshot_path)}" data-site="${escapeHtml(r.site_name || '')}" style="color:var(--accent); font-size:12px;">View</a>`
                    : '<span style="color:var(--text-muted); font-size:12px;">—</span>';

                let diffCell;
                if (r.diff_score !== null && r.diff_score !== undefined) {
                    const score = parseFloat(r.diff_score);
                    const threshold = r.diff_threshold ?? 1.0;
                    const diffColor = score === 0 ? 'var(--green)'
                        : score < threshold ? 'var(--yellow)' : 'var(--red)';
                    const hasCompare = r.diff_screenshot_path && r.prev_screenshot_path;
                    diffCell = `<span class="${hasCompare ? 'diff-score-link' : ''}"
                        style="color:${diffColor}; font-size:12px; font-weight:600; ${hasCompare ? 'cursor:pointer; text-decoration:underline;' : ''}"
                        data-prev="${r.prev_screenshot_path ? encodeURIComponent(r.prev_screenshot_path) : ''}"
                        data-diff="${r.diff_screenshot_path ? encodeURIComponent(r.diff_screenshot_path) : ''}"
                        data-current="${r.screenshot_path ? encodeURIComponent(r.screenshot_path) : ''}"
                        data-site="${escapeHtml(r.site_name || '')}"
                        data-score="${score}"
                        data-page-url="${encodeURIComponent(r.page_url || r.site_url || '')}"
                        >${score === 0 ? '0% — no change' : score.toFixed(2) + '%'}</span>`;
                } else {
                    diffCell = '<span style="color:var(--text-muted); font-size:12px;">—</span>';
                }

                const ts = r.run_started_at || r.checked_at;
                const dateStr = ts ? new Date(ts).toLocaleDateString('en-US',
                    { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';

                const jsDetailRow = jsErrs.length ? `
                    <tr id="${idPrefix}-js-${idx}" style="display:none; background:var(--surface-2);">
                        <td></td><td colspan="7" style="padding:6px 12px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">JS ERRORS</div>
                            ${jsErrs.map(e => `<div style="font-family:monospace; font-size:11px; color:var(--red); margin-bottom:2px;">${escapeHtml(String(e))}</div>`).join('')}
                        </td>
                    </tr>` : '';

                const brokenDetailRow = broken.length ? `
                    <tr id="${idPrefix}-br-${idx}" style="display:none; background:var(--surface-2);">
                        <td></td><td colspan="7" style="padding:6px 12px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">BROKEN RESOURCES</div>
                            ${broken.map(b => `<div style="font-family:monospace; font-size:11px; margin-bottom:2px;"><span style="color:var(--red); font-weight:500;">${b.status}</span> <span style="color:var(--text-muted);">${escapeHtml(b.url)}</span></div>`).join('')}
                        </td>
                    </tr>` : '';

                return `<tr style="${rowBg}">
                    <td style="text-align:center;">${icon}</td>
                    <td style="font-size:12px; color:var(--text-muted);">${dateStr}</td>
                    <td style="color:${httpColor}; font-weight:500;">${r.http_status || '—'}</td>
                    <td style="color:${loadColor};">${loadStr}</td>
                    <td>${jsCell}</td>
                    <td>${brokenCell}</td>
                    <td style="text-align:center;">${screenshotHtml}</td>
                    <td style="text-align:center;">${diffCell}</td>
                </tr>${jsDetailRow}${brokenDetailRow}`;
            }).join('');

            tbody.querySelectorAll('.screenshot-link').forEach(link => {
                link.addEventListener('click', e => {
                    e.preventDefault();
                    document.getElementById('screenshotImg').src = link.dataset.src;
                    document.getElementById('screenshotTitle').textContent = link.dataset.site;
                    document.getElementById('screenshotOverlay').style.display = 'flex';
                    modalPush('screenshotOverlay', closeScreenshotOverlay);
                });
            });

            tbody.querySelectorAll('.reg-detail-toggle').forEach(el => {
                el.addEventListener('click', () => {
                    const target = document.getElementById(el.dataset.target);
                    if (target) {
                        const visible = target.style.display !== 'none';
                        target.style.display = visible ? 'none' : '';
                        el.style.fontWeight = visible ? 'normal' : '700';
                    }
                });
            });

            tbody.querySelectorAll('.diff-score-link').forEach(el => {
                el.addEventListener('click', () => {
                    const pageUrl = decodeURIComponent(el.dataset.pageUrl || '');
                    const prevPath = decodeURIComponent(el.dataset.prev || '');
                    const diffPath = decodeURIComponent(el.dataset.diff || '');
                    const currentPath = decodeURIComponent(el.dataset.current || '');
                    const score = el.dataset.score;
                    document.getElementById('diffTitle').textContent = `Visual Diff — ${el.dataset.site}`;
                    document.getElementById('diffSubtitle').textContent =
                        `${pageUrl || ''}  •  ${parseFloat(score).toFixed(2)}% pixels changed`;
                    document.getElementById('diffBaselineImg').src = prevPath ? `/api/regression/screenshot/${prevPath}` : '';
                    document.getElementById('diffDiffImg').src = diffPath ? `/api/regression/screenshot/${diffPath}` : '';
                    document.getElementById('diffCurrentImg').src = currentPath ? `/api/regression/screenshot/${currentPath}` : '';
                    document.getElementById('diffOverlay').style.display = 'flex';
                    modalPush('diffOverlay', closeDiffOverlay);
                });
            });
        }

        async function loadSiteRegHistory(siteId) {
            const tbody = document.getElementById('siteHistoryBody');
            const emptyEl = document.getElementById('siteHistoryEmpty');
            tbody.innerHTML = `<tr><td colspan="8" style="color:var(--text-muted); padding:20px; text-align:center;">Loading…</td></tr>`;
            emptyEl.style.display = 'none';
            try {
                const resp = await fetch(`/api/regression/site/${siteId}/history`);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const results = await resp.json();
                renderRegSiteHistoryInto({ tbody, emptyEl }, results);
            } catch (e) {
                tbody.innerHTML = `<tr><td colspan="8" style="color:var(--red); padding:12px;">Error: ${e.message}</td></tr>`;
            }
        }

        document.getElementById('siteHistoryBackBtn').addEventListener('click', () => {
            navigate({ tab: 'regression', subview: 'sites' });
        });

        export async function checkRegressionAvailability() {
            try {
                const resp = await fetch('/api/regression/status');
                const data = await resp.json();
                if (!data.available) {
                    document.getElementById('regressionUnavailable').style.display = '';
                    document.getElementById('regressionRunBtn').disabled = true;
                    document.getElementById('regressionRunBtn').style.opacity = '0.4';
                }
                // If a run is in progress (e.g. page was reloaded mid-run), resume polling
                if (data.active_run) {
                    showRegressionProgress(data.active_run);
                    startRegressionPolling();
                }
            } catch (e) {
                // Ignore — server might not be running yet
            }
        }

        async function startRegressionRun() {
            const btn = document.getElementById('regressionRunBtn');
            const status = document.getElementById('regressionStatus');

            // Validate at least one site is selected
            if (_regSiteList.length && _regSelectedIds.size === 0) {
                status.textContent = 'No sites selected — click "Sites" to choose which sites to check.';
                return;
            }

            btn.disabled = true;
            status.textContent = 'Starting...';

            // Send selected site IDs (empty array = all)
            const body = {};
            if (_regSiteList.length && _regSelectedIds.size < _regSiteList.length) {
                body.site_ids = [..._regSelectedIds];
            }

            try {
                const resp = await fetch('/api/regression/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await resp.json();
                if (data.error) {
                    status.textContent = data.error;
                    btn.disabled = false;
                    return;
                }
                status.textContent = '';
                showRegressionProgress({ total: data.total_sites, checked: 0, issues_found: 0 });
                startRegressionPolling();
            } catch (e) {
                status.textContent = `Error: ${e.message}`;
                btn.disabled = false;
            }
        }

        async function cancelRegressionRun() {
            const btn = document.getElementById('regressionCancelBtn');
            btn.disabled = true;
            btn.textContent = 'Cancelling...';
            try {
                const resp = await fetch('/api/regression/cancel', { method: 'POST' });
                const data = await resp.json();
                if (data.error) {
                    document.getElementById('regressionStatus').textContent = data.error;
                }
                // Polling will detect the run ended and clean up UI
            } catch (e) {
                document.getElementById('regressionStatus').textContent = `Cancel failed: ${e.message}`;
            }
        }

        let _currentViewRunId = null; // tracks which run is displayed

        async function deleteRegressionRun(runId) {
            if (!runId) return;
            if (!confirm('Delete this regression run and its screenshots?')) return;
            try {
                const resp = await fetch(`/api/regression/run/${runId}`, { method: 'DELETE' });
                const data = await resp.json();
                if (data.error) {
                    document.getElementById('regressionStatus').textContent = data.error;
                    return;
                }
                document.getElementById('regressionStatus').textContent = 'Run deleted.';
                _currentViewRunId = null;
                document.getElementById('regressionDeleteBtn').style.display = 'none';
                // Reload the run list and show the latest remaining
                await loadRegressionRuns();
                const sel = document.getElementById('regressionRunSelect');
                if (sel.options.length) {
                    loadRegressionResults(sel.value);
                } else {
                    // No runs left
                    document.getElementById('regressionBody').innerHTML = '';
                    document.getElementById('regressionEmpty').style.display = '';
                    document.getElementById('regressionSummary').style.display = 'none';
                }
            } catch (e) {
                document.getElementById('regressionStatus').textContent = `Delete failed: ${e.message}`;
            }
        }

        function showRegressionProgress(run) {
            const prog = document.getElementById('regressionProgress');
            prog.style.display = '';
            document.getElementById('regressionRunBtn').style.display = 'none';
            const cancelBtn = document.getElementById('regressionCancelBtn');
            cancelBtn.style.display = '';
            cancelBtn.disabled = false;
            cancelBtn.textContent = '✕ Cancel';
            document.getElementById('regressionDeleteBtn').style.display = 'none';
            updateRegressionProgress(run);
        }

        function updateRegressionProgress(run) {
            const total = run.total || 1;
            const checked = run.checked || 0;
            const pct = Math.round((checked / total) * 100);
            document.getElementById('regressionProgressBar').style.width = pct + '%';
            document.getElementById('regressionProgressCount').textContent = `${checked} / ${total}`;
            const label = run.current_site
                ? `Checking: ${run.current_site}`
                : 'Checking sites...';
            document.getElementById('regressionProgressLabel').textContent = label;
        }

        function startRegressionPolling() {
            if (_regressionPollTimer) clearInterval(_regressionPollTimer);
            _regressionPollTimer = setInterval(pollRegressionStatus, 2000);
        }

        async function pollRegressionStatus() {
            try {
                const resp = await fetch('/api/regression/status');
                const data = await resp.json();
                if (data.active_run) {
                    updateRegressionProgress(data.active_run);
                } else {
                    // Run finished — stop polling, load results
                    clearInterval(_regressionPollTimer);
                    _regressionPollTimer = null;
                    document.getElementById('regressionProgress').style.display = 'none';
                    document.getElementById('regressionRunBtn').style.display = '';
                    document.getElementById('regressionRunBtn').disabled = false;
                    document.getElementById('regressionCancelBtn').style.display = 'none';
                    if (_regViewMode === 'sites') loadSiteStatus();
                    else loadRegressionResults();
                }
            } catch (e) { /* ignore polling errors */ }
        }

        // ── Load historical runs into the run selector ─────────────────────────
        async function loadRegressionRuns() {
            const sel = document.getElementById('regressionRunSelect');
            try {
                const resp = await fetch('/api/regression/runs');
                const runs = await resp.json();
                if (!runs.length) { sel.style.display = 'none'; return; }
                sel.style.display = '';
                const currentVal = sel.value; // preserve selection before rebuild
                sel.innerHTML = runs.map((r, i) => {
                    const d = new Date(r.started_at);
                    const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                               + ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
                               + ` — ${r.total_sites} sites, ${r.issues_found} issues`
                               + (r.status === 'failed' ? ' (failed)' : '');
                    return `<option value="${r.id}">${label}</option>`;
                }).join('');
                // Restore previous selection if it still exists in the list, else default to first
                if (currentVal && sel.querySelector(`option[value="${currentVal}"]`)) {
                    sel.value = currentVal;
                }
            } catch (e) { /* ignore */ }
        }

        // ── Load results (latest or by run_id) ──────────────────────────────────
        async function loadRegressionResults(runId) {
            try {
                const url = runId
                    ? `/api/regression/results/${runId}`
                    : '/api/regression/latest';
                const resp = await fetch(url);
                if (!resp.ok) {
                    document.getElementById('regressionEmpty').style.display = '';
                    document.getElementById('regressionSummary').style.display = 'none';
                    return;
                }
                const data = await resp.json();
                // /latest returns {results, started_at, ...}; /results/{id} returns an array
                const run = Array.isArray(data) ? { results: data } : data;
                renderRegressionResults(run);
                loadRegressionRuns(); // refresh the run selector
            } catch (e) {
                document.getElementById('regressionStatus').textContent = `Error loading results: ${e.message}`;
            }
        }

        // ── Run selector change handler ──────────────────────────────────────────
        document.getElementById('regressionRunSelect').addEventListener('change', e => {
            history.replaceState(null, '', buildHash({ tab: 'regression', subview: 'runs', id: e.target.value }));
            loadRegressionResults(e.target.value);
        });

        let _currentRunData = null;
        let _lastSiteStatusResults = [];
        let _regSiteSort = { col: null, dir: 1 };   // null col = default (issues-first)
        let _regRunSort  = { col: null, dir: 1 };


        function _sortRegData(arr, col, dir) {
            if (!col) return arr;
            return [...arr].sort((a, b) => {
                let av, bv;
                switch (col) {
                    case 'status': av = a.has_issues ? 1 : 0; bv = b.has_issues ? 1 : 0; break;
                    case 'site':   av = (a.site_name || '').toLowerCase(); bv = (b.site_name || '').toLowerCase(); break;
                    case 'http':   av = a.http_status || 0;  bv = b.http_status || 0;  break;
                    case 'load':   av = a.load_time_ms || 0; bv = b.load_time_ms || 0; break;
                    case 'js':     av = safeJsonParse(a.js_errors, []).length; bv = safeJsonParse(b.js_errors, []).length; break;
                    case 'broken': av = safeJsonParse(a.broken_resources, []).length; bv = safeJsonParse(b.broken_resources, []).length; break;
                    case 'diff':   av = a.diff_score ?? -1;  bv = b.diff_score ?? -1;  break;
                    case 'checked': av = a.run_started_at || a.checked_at || ''; bv = b.run_started_at || b.checked_at || ''; break;
                    default: return 0;
                }
                if (av < bv) return -dir;
                if (av > bv) return dir;
                return 0;
            });
        }

        function renderRegressionResults(run) {
            _currentRunData = run;
            const results = run.results || [];
            const tbody = document.getElementById('regressionBody');
            const empty = document.getElementById('regressionEmpty');
            const summary = document.getElementById('regressionSummary');
            const info = document.getElementById('regressionRunInfo');
            const deleteBtn = document.getElementById('regressionDeleteBtn');

            // Restore per-run table header (sortable)
            const thead = document.querySelector('#regressionTable thead tr');
            thead.innerHTML =
                `<th style="width:40px;">Status</th>` +
                _sortTh('Site', 'site', _regRunSort) +
                _sortTh('HTTP', 'http', _regRunSort) +
                _sortTh('Load Time', 'load', _regRunSort) +
                _sortTh('JS Errors', 'js', _regRunSort) +
                _sortTh('Broken Resources', 'broken', _regRunSort) +
                `<th style="width:80px;">Screenshot</th>` +
                _sortTh('Diff', 'diff', _regRunSort, 'width:90px;');

            // Wire header sort clicks
            thead.querySelectorAll('th[data-sort-col]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.sortCol;
                    if (_regRunSort.col === col) _regRunSort.dir *= -1;
                    else { _regRunSort.col = col; _regRunSort.dir = 1; }
                    renderRegressionResults(_currentRunData);
                });
            });

            _currentViewRunId = run.id || null;

            if (!results.length) {
                tbody.innerHTML = '';
                empty.style.display = '';
                summary.style.display = 'none';
                deleteBtn.style.display = 'none';
                return;
            }

            empty.style.display = 'none';
            summary.style.display = '';
            // Show delete button when viewing a completed run
            deleteBtn.style.display = _currentViewRunId ? '' : 'none';

            // Summary stats
            const total = results.length;
            const withIssues = results.filter(r => r.has_issues).length;
            const clean = total - withIssues;
            const loadTimes = results.filter(r => r.load_time_ms).map(r => r.load_time_ms);
            const avgLoad = loadTimes.length
                ? Math.round(loadTimes.reduce((a, b) => a + b, 0) / loadTimes.length)
                : null;

            document.getElementById('regStatTotal').textContent = total;
            document.getElementById('regStatClean').textContent = clean;
            document.getElementById('regStatIssues').textContent = withIssues;
            document.getElementById('regStatAvgLoad').textContent = avgLoad ? `${(avgLoad / 1000).toFixed(1)}s` : '—';

            // Run timestamp
            if (run.started_at) {
                const d = new Date(run.started_at);
                info.textContent = `Run: ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} at ${d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
            }

            // Apply sort (default: issues first, then alpha by site name)
            let sorted;
            if (_regRunSort.col) {
                sorted = _sortRegData(results, _regRunSort.col, _regRunSort.dir);
            } else {
                sorted = [...results].sort((a, b) => {
                    const ai = a.has_issues ? 0 : 1, bi = b.has_issues ? 0 : 1;
                    if (ai !== bi) return ai - bi;
                    return (a.site_name || '').toLowerCase().localeCompare((b.site_name || '').toLowerCase());
                });
            }

            // Render table rows with expandable detail rows
            tbody.innerHTML = sorted.map((r, idx) => {
                const jsErrors = safeJsonParse(r.js_errors, []);
                const broken = safeJsonParse(r.broken_resources, []);
                const hasIssue = r.has_issues;
                const icon = hasIssue ? '⚠️' : '✅';
                const hasHttpError = r.http_status && r.http_status >= 400;
                const hasJsOrBroken = jsErrors.length > 0 || broken.length > 0;
                const rowBg = hasHttpError
                    ? 'background: var(--red-bg);'
                    : hasJsOrBroken
                        ? 'background: var(--yellow-bg);'
                        : '';

                // HTTP status color
                const httpColor = !r.http_status ? 'var(--text-muted)'
                    : r.http_status < 300 ? 'var(--green)'
                    : r.http_status < 400 ? 'var(--yellow)'
                    : 'var(--red)';

                // Load time color
                const lt = r.load_time_ms;
                const loadStr = lt ? `${(lt / 1000).toFixed(1)}s` : '—';
                const loadColor = !lt ? 'var(--text-muted)'
                    : lt > 10000 ? 'var(--red)'
                    : lt > 5000 ? 'var(--yellow)'
                    : 'var(--text)';

                // JS errors — count with expandable detail
                let jsCell;
                if (jsErrors.length) {
                    jsCell = `<span class="reg-detail-toggle" data-target="reg-js-${idx}" style="color:var(--red); cursor:pointer; text-decoration:underline;">${jsErrors.length} error${jsErrors.length > 1 ? 's' : ''}</span>`;
                } else {
                    jsCell = '<span style="color:var(--text-muted);">None</span>';
                }

                // Broken resources — count with expandable detail
                let brokenCell;
                if (broken.length) {
                    brokenCell = `<span class="reg-detail-toggle" data-target="reg-br-${idx}" style="color:var(--red); cursor:pointer; text-decoration:underline;">${broken.length} broken</span>`;
                } else {
                    brokenCell = '<span style="color:var(--text-muted);">None</span>';
                }

                // Screenshot link
                const screenshotHtml = r.screenshot_path
                    ? `<a href="#" class="screenshot-link" data-src="/api/regression/screenshot/${encodeURIComponent(r.screenshot_path)}" data-site="${escapeHtml(r.site_name)}" style="color:var(--accent); font-size:12px;">View</a>`
                    : '<span style="color:var(--text-muted); font-size:12px;">—</span>';

                // Diff cell — auto-compare to previous run, no manual baseline needed
                let diffCell;
                if (r.diff_score !== null && r.diff_score !== undefined) {
                    const score = parseFloat(r.diff_score);
                    const threshold = r.diff_threshold ?? 1.0;
                    const diffColor = score === 0 ? 'var(--green)'
                        : score < threshold ? 'var(--yellow)'
                        : 'var(--red)';
                    const hasCompare = r.diff_screenshot_path && r.prev_screenshot_path;
                    diffCell = `<span class="${hasCompare ? 'diff-score-link' : ''}"
                        style="color:${diffColor}; font-size:12px; font-weight:600; ${hasCompare ? 'cursor:pointer; text-decoration:underline;' : ''}"
                        data-prev="${r.prev_screenshot_path ? encodeURIComponent(r.prev_screenshot_path) : ''}"
                        data-diff="${r.diff_screenshot_path ? encodeURIComponent(r.diff_screenshot_path) : ''}"
                        data-current="${r.screenshot_path ? encodeURIComponent(r.screenshot_path) : ''}"
                        data-site="${escapeHtml(r.site_name)}"
                        data-score="${score}"
                        data-result-id="${r.id}"
                        data-site-id="${r.site_id}"
                        data-page-url="${escapeHtml(r.page_url || r.site_url || '')}"
                        >${score === 0 ? '0% — no change' : score.toFixed(2) + '%'}</span>`;
                } else {
                    diffCell = '<span style="color:var(--text-muted); font-size:12px;">—</span>';
                }

                // Build main row
                let html = `<tr style="${rowBg}">
                    <td style="text-align:center;">${icon}</td>
                    <td>
                        <div style="font-weight:500;">${escapeHtml(r.site_name || '—')}</div>
                        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml((r.page_url || r.site_url || '').replace(/^https?:\/\//, ''))}</div>
                        ${r.error ? `<div style="font-size:11px; color:var(--red); margin-top:2px;">${escapeHtml(r.error)}</div>` : ''}
                    </td>
                    <td style="color:${httpColor}; font-weight:500;">${r.http_status || '—'}</td>
                    <td style="color:${loadColor};">${loadStr}</td>
                    <td>${jsCell}</td>
                    <td>${brokenCell}</td>
                    <td style="text-align:center;">${screenshotHtml}</td>
                    <td style="text-align:center;">${diffCell}</td>
                </tr>`;

                // JS errors detail row (hidden by default)
                if (jsErrors.length) {
                    html += `<tr id="reg-js-${idx}" class="reg-detail-row" style="display:none;">
                        <td></td>
                        <td colspan="7" style="padding:8px 14px; background:var(--surface-2); border-radius:4px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">JS Console Errors</div>
                            ${jsErrors.map(e => `<div style="font-size:12px; color:var(--red); font-family:'SF Mono',SFMono-Regular,monospace; padding:3px 0; word-break:break-all;">${escapeHtml(e)}</div>`).join('')}
                        </td>
                    </tr>`;
                }

                // Broken resources detail row (hidden by default)
                if (broken.length) {
                    html += `<tr id="reg-br-${idx}" class="reg-detail-row" style="display:none;">
                        <td></td>
                        <td colspan="7" style="padding:8px 14px; background:var(--surface-2); border-radius:4px;">
                            <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-bottom:4px;">Broken Resources</div>
                            ${broken.map(b => `<div style="font-size:12px; font-family:'SF Mono',SFMono-Regular,monospace; padding:3px 0; word-break:break-all;"><span style="color:var(--red); font-weight:500;">${b.status}</span> <span style="color:var(--text-muted);">${escapeHtml(b.url)}</span></div>`).join('')}
                        </td>
                    </tr>`;
                }

                return html;
            }).join('');

            // Wire expandable detail toggles
            tbody.querySelectorAll('.reg-detail-toggle').forEach(toggle => {
                toggle.addEventListener('click', () => {
                    const target = document.getElementById(toggle.dataset.target);
                    if (target) {
                        const visible = target.style.display !== 'none';
                        target.style.display = visible ? 'none' : '';
                        toggle.style.fontWeight = visible ? 'normal' : '700';
                    }
                });
            });

            // Wire screenshot links
            tbody.querySelectorAll('.screenshot-link').forEach(link => {
                link.addEventListener('click', e => {
                    e.preventDefault();
                    document.getElementById('screenshotImg').src = link.dataset.src;
                    document.getElementById('screenshotTitle').textContent = link.dataset.site;
                    document.getElementById('screenshotOverlay').style.display = 'flex';
                    modalPush('screenshotOverlay', closeScreenshotOverlay);
                });
            });

            // Wire diff score click → comparison viewer
            tbody.querySelectorAll('.diff-score-link').forEach(el => {
                el.addEventListener('click', () => {
                    const pageUrl = decodeURIComponent(el.dataset.pageUrl || '');
                    const prevPath = decodeURIComponent(el.dataset.prev || '');
                    const diffPath = decodeURIComponent(el.dataset.diff || '');
                    const currentPath = decodeURIComponent(el.dataset.current || '');
                    const score = el.dataset.score;

                    document.getElementById('diffTitle').textContent =
                        `Visual Diff — ${el.dataset.site}`;
                    document.getElementById('diffSubtitle').textContent =
                        `${pageUrl || ''}  •  ${parseFloat(score).toFixed(2)}% pixels changed`;

                    document.getElementById('diffBaselineImg').src = prevPath
                        ? `/api/regression/screenshot/${prevPath}`
                        : '';
                    document.getElementById('diffDiffImg').src = diffPath
                        ? `/api/regression/screenshot/${diffPath}`
                        : '';
                    document.getElementById('diffCurrentImg').src = currentPath
                        ? `/api/regression/screenshot/${currentPath}`
                        : '';

                    document.getElementById('diffOverlay').style.display = 'flex';
                    modalPush('diffOverlay', closeDiffOverlay);
                });
            });
        }


        // Close screenshot overlay
        function closeScreenshotOverlay() {
            document.getElementById('screenshotOverlay').style.display = 'none';
            document.getElementById('screenshotImg').src = '';
            modalRemove('screenshotOverlay');
        }

        // Close diff overlay
        function closeDiffOverlay() {
            document.getElementById('diffOverlay').style.display = 'none';
            modalRemove('diffOverlay');
        }

        document.getElementById('diffCloseBtn').addEventListener('click', closeDiffOverlay);
        document.getElementById('diffOverlay').addEventListener('click', e => {
            if (e.target === document.getElementById('diffOverlay')) closeDiffOverlay();
        });

        // ── Site selector state ───────────────────────────────────────────────────
        let _regSiteList = [];          // [{id, name, url}, ...] — all available sites
        let _regSelectedIds = new Set(); // site IDs currently checked
        const REG_DEFAULTS_KEY = 'wsp_reg_defaults';
        let _regSetDefaultTimer = null; // auto-cancel timer for Set Default confirm step

        function _saveSelectedSites() {
            try {
                // Always save as strings for consistent round-trip behaviour
                localStorage.setItem('wsp_reg_selected', JSON.stringify([..._regSelectedIds].map(String)));
            } catch { /* ignore */ }
        }

        function _loadSelectedSites() {
            try {
                const saved = JSON.parse(localStorage.getItem('wsp_reg_selected') || '[]');
                // Always normalise to strings to avoid int/string Set mismatch
                if (Array.isArray(saved) && saved.length) return new Set(saved.map(String));
            } catch { /* ignore */ }
            return null;
        }

        export async function loadRegressionSiteList() {
            // Fetch sites from cache API (same source the server uses)
            try {
                const resp = await fetch('/api/mainwp/sites');
                if (!resp.ok) return;
                const data = await resp.json();
                const sites = Array.isArray(data) ? data : (data.data || data.sites || []);
                _regSiteList = sites
                    // Normalise IDs to strings — avoids int/string mismatches in Set operations
                    .map(s => ({ id: String(s.id), name: s.name || 'Unknown', url: s.url || '' }))
                    .sort((a, b) => a.name.localeCompare(b.name));
                // Restore last selection, or default to all selected
                const saved = _loadSelectedSites();
                if (saved) {
                    // Filter to only IDs that still exist in the current site list
                    const validIds = new Set(_regSiteList.map(s => s.id));
                    _regSelectedIds = new Set([...saved].filter(id => validIds.has(id)));
                    // If saved selection is now empty (all sites removed?), select all
                    if (_regSelectedIds.size === 0) _regSelectedIds = new Set(_regSiteList.map(s => s.id));
                } else {
                    _regSelectedIds = new Set(_regSiteList.map(s => s.id));
                }
                updateSiteCountBadge();
            } catch (e) { /* ignore */ }
        }

        function updateSiteCountBadge() {
            const total = _regSiteList.length;
            const sel = _regSelectedIds.size;
            const badge = document.getElementById('regressionSiteCount');
            badge.textContent = sel === total ? `(${total})` : `(${sel}/${total})`;
            // Also update count inside the modal
            const modalCount = document.getElementById('siteSelectCount');
            if (modalCount) modalCount.textContent = `${sel} of ${total} selected`;
        }

        function renderSiteSelectList() {
            const listEl = document.getElementById('siteSelectList');
            listEl.innerHTML = _regSiteList.map(s => {
                const checked = _regSelectedIds.has(s.id) ? 'checked' : '';
                return `<label style="display:flex; align-items:center; gap:10px; padding:7px 18px; cursor:pointer; font-size:13px;" class="site-select-row">
                    <input type="checkbox" data-site-id="${s.id}" ${checked}
                           style="accent-color:var(--accent); width:15px; height:15px; cursor:pointer; flex-shrink:0;">
                    <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(s.name)}</span>
                    <span style="font-size:11px; color:var(--text-muted); flex-shrink:0;">${escapeHtml(s.url.replace(/^https?:\/\//, ''))}</span>
                </label>`;
            }).join('');

            // Wire individual checkboxes
            listEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', () => {
                    const siteId = cb.dataset.siteId; // always a string — matches _regSiteList IDs
                    if (cb.checked) _regSelectedIds.add(siteId);
                    else _regSelectedIds.delete(siteId);
                    syncSelectAllCheckbox();
                    updateSiteCountBadge();
                    _saveSelectedSites();
                });
            });

            syncSelectAllCheckbox();
            updateSiteCountBadge();
        }

        function syncSelectAllCheckbox() {
            const allCb = document.getElementById('siteSelectAll');
            allCb.checked = _regSelectedIds.size === _regSiteList.length;
            allCb.indeterminate = _regSelectedIds.size > 0 && _regSelectedIds.size < _regSiteList.length;
        }

        // Select All toggle
        document.getElementById('siteSelectAll').addEventListener('change', e => {
            const check = e.target.checked;
            _regSelectedIds = check ? new Set(_regSiteList.map(s => s.id)) : new Set();
            document.querySelectorAll('#siteSelectList input[type="checkbox"]').forEach(cb => {
                cb.checked = check;
            });
            updateSiteCountBadge();
            _saveSelectedSites();
        });

        // Open modal
        document.getElementById('regressionSiteSelectBtn').addEventListener('click', () => {
            renderSiteSelectList();
            document.getElementById('siteSelectOverlay').style.display = 'flex';
            modalPush('siteSelectOverlay', closeSiteSelectModal);
        });

        // Close modal
        function closeSiteSelectModal() {
            // Reset Set Default button if it was mid-confirm
            const setBtn = document.getElementById('regSetDefaultBtn');
            if (setBtn.dataset.state === 'confirm') {
                clearTimeout(_regSetDefaultTimer);
                setBtn.dataset.state  = 'idle';
                setBtn.textContent    = 'Set Default';
                setBtn.style.background   = '';
                setBtn.style.borderColor  = '';
                setBtn.style.color        = '';
            }
            document.getElementById('siteSelectOverlay').style.display = 'none';
            modalRemove('siteSelectOverlay');
        }
        document.getElementById('siteSelectCloseBtn').addEventListener('click', closeSiteSelectModal);
        document.getElementById('siteSelectDoneBtn').addEventListener('click', closeSiteSelectModal);
        document.getElementById('siteSelectOverlay').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeSiteSelectModal();
        });

        // ── Default options (Restore / Set) ──────────────────────────────────────

        /** Apply saved or factory defaults to the site selection. */
        function restoreRegDefaults() {
            let defaults = null;
            try {
                defaults = JSON.parse(localStorage.getItem(REG_DEFAULTS_KEY) || 'null');
            } catch { /* ignore */ }

            if (defaults && defaults.site_ids === null) {
                // null means "all sites"
                _regSelectedIds = new Set(_regSiteList.map(s => s.id));
            } else if (defaults && Array.isArray(defaults.site_ids)) {
                const valid = new Set(_regSiteList.map(s => s.id));
                _regSelectedIds = new Set(
                    defaults.site_ids.filter(id => valid.has(String(id))).map(String)
                );
                if (_regSelectedIds.size === 0) {
                    _regSelectedIds = new Set(_regSiteList.map(s => s.id));
                }
            } else {
                // Factory default: all sites
                _regSelectedIds = new Set(_regSiteList.map(s => s.id));
            }

            // Persist and refresh UI
            _saveSelectedSites();
            renderSiteSelectList();
            updateSiteCountBadge();
        }

        /** Save the current site selection as the new default. */
        function regSaveAsDefault() {
            localStorage.setItem(REG_DEFAULTS_KEY, JSON.stringify({
                // null = all sites (resilient to future site additions)
                site_ids: _regSelectedIds.size === _regSiteList.length
                    ? null
                    : [..._regSelectedIds],
            }));
        }

        document.getElementById('regRestoreDefaultBtn').addEventListener('click', () => {
            restoreRegDefaults();
            const btn = document.getElementById('regRestoreDefaultBtn');
            btn.textContent = 'Restored ✓';
            btn.style.color = 'var(--green)';
            setTimeout(() => {
                btn.textContent = 'Restore Default';
                btn.style.color = '';
            }, 1800);
        });

        document.getElementById('regSetDefaultBtn').addEventListener('click', () => {
            const btn = document.getElementById('regSetDefaultBtn');
            if (btn.dataset.state === 'confirm') {
                // Second click — confirmed
                clearTimeout(_regSetDefaultTimer);
                regSaveAsDefault();
                btn.dataset.state  = 'idle';
                btn.textContent    = 'Saved ✓';
                btn.style.background  = '';
                btn.style.borderColor = '';
                btn.style.color       = 'var(--green)';
                _regSetDefaultTimer = setTimeout(() => {
                    btn.textContent = 'Set Default';
                    btn.style.color = '';
                }, 1800);
            } else {
                // First click — enter confirm state (auto-cancels after 3 s)
                btn.dataset.state  = 'confirm';
                btn.textContent    = 'Confirm?';
                btn.style.background  = 'var(--accent)';
                btn.style.borderColor = 'var(--accent)';
                btn.style.color       = '#fff';
                _regSetDefaultTimer = setTimeout(() => {
                    btn.dataset.state  = 'idle';
                    btn.textContent    = 'Set Default';
                    btn.style.background  = '';
                    btn.style.borderColor = '';
                    btn.style.color       = '';
                }, 3000);
            }
        });

        // Wire view toggle buttons
        document.getElementById('regViewSites').addEventListener('click', () => {
            history.replaceState(null, '', buildHash({ tab: 'regression', subview: 'sites' }));
            _saveLastSubview('regression', 'sites');
            setRegView('sites');
        });
        document.getElementById('regViewRuns').addEventListener('click', () => {
            history.replaceState(null, '', buildHash({ tab: 'regression', subview: 'runs' }));
            _saveLastSubview('regression', 'runs');
            setRegView('runs');
        });

        // Wire the Run, Cancel, and Delete buttons
        document.getElementById('regressionRunBtn').addEventListener('click', startRegressionRun);
        document.getElementById('regressionCancelBtn').addEventListener('click', cancelRegressionRun);
        document.getElementById('regressionDeleteBtn').addEventListener('click', () => {
            deleteRegressionRun(_currentViewRunId);
        });

        // Wire screenshot close
        document.getElementById('screenshotCloseBtn').addEventListener('click', closeScreenshotOverlay);
        document.getElementById('screenshotOverlay').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeScreenshotOverlay();
        });


        // ============================================================================
        // Regression sub-view route handler
        // ============================================================================
        // Moved here (from app.js's former "Sub-view route handlers" section) now
        // that all the state and functions it touches (_lastSiteStatusResults,
        // _regViewMode, _currentHistorySiteId, _pendingRegSiteHistory, setRegView,
        // openSiteRegHistory, closeSiteRegHistory) live in this module. Registers
        // its own route so app.js/router.js need no regression-specific knowledge.
        let _pendingRegSiteHistory = null; // site_id — set when routing to regression/sites/{id} before data loads

        function _applyRegSubview(subview, id) {
            if (subview === 'runs') {
                setRegView('runs', id || null);
                return;
            }
            // subview === 'sites' (possibly with a site-id for drill-down)
            if (id) {
                const sid = String(id);
                const site = _lastSiteStatusResults.find(r => String(r.site_id) === sid);
                if (site && _regViewMode === 'sites') {
                    // Data already loaded — open drill-down directly
                    openSiteRegHistory(sid, site.site_name || '', site.page_url || site.site_url || '');
                } else {
                    // Load sites list first; pending flag applied after render
                    _pendingRegSiteHistory = sid;
                    if (_currentHistorySiteId) closeSiteRegHistory();
                    setRegView('sites');
                }
            } else {
                if (_currentHistorySiteId) closeSiteRegHistory();
                setRegView('sites');
            }
        }

        registerRoute('regression', (subview, id) => _applyRegSubview(subview, id));
