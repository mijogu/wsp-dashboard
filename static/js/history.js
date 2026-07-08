// ============================================================================
// Update History Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { apiCall } from './api.js';
import { escapeHtml } from './dom.js';
import { registerRoute } from './router.js';

import { dbg } from './debug.js';
dbg('module', 'history.js loaded');

export let historyData = [];
let lastFetchDate = null; // populated by loadDbStats

registerRoute('history', () => { loadDbStats(); if (!historyData.length) loadCachedHistory(true); });

// ── Sync Updates (incremental) ───────────────────────────────────────────
async function runHistorySync(startDate, endDate) {
    const btn = document.getElementById('historySyncBtn');
    const status = document.getElementById('historyStatus');

    btn.disabled = true;
    btn.textContent = '⟳ Syncing…';
    status.textContent = 'Querying Pro Reports across all sites…';

    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate)   params.set('end_date',   endDate);

    try {
        const data = await apiCall(`/mainwp/update-history?${params}`);
        const newRecs = data.total_records || 0;
        const mode = data.sync_mode || 'fetch';
        status.textContent =
            `${newRecs} records fetched (${data.date_from} → ${data.date_to}) `
            + `across ${data.sites_queried} sites`;

        // Reload cached view and stats after sync
        await loadCachedHistory(true);
        await loadDbStats();

    } catch (e) {
        status.textContent = `Error: ${e.message}`;
        console.error('History sync error:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = '⟳ Sync Updates';
    }
}

document.getElementById('historySyncBtn').addEventListener('click', () => {
    runHistorySync(null, null); // let server decide dates (incremental)
});

// ── Export CSV (always matches exactly what's shown in the table) ──────────
document.getElementById('historyExportBtn').addEventListener('click', () => {
    if (!_filteredRecords.length) return;
    const headers = ['Site', 'Type', 'Name', 'Old Version', 'New Version', 'Date'];
    const escape  = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const rows = _filteredRecords.map(r => [
        escape(r._site_name || ''),
        escape(r._update_type || ''),
        escape(r.name || r.title || r.slug || ''),
        escape(r.old_version || ''),
        escape(r.current_version || r.new_version || ''),
        escape(formatDateOnly(r)),
    ].join(','));
    const csv  = [headers.join(','), ...rows].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `update-history-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
});

// ── Backfill Panel ───────────────────────────────────────────────────────
const backfillPanel  = document.getElementById('historyBackfillPanel');
const backfillToggle = document.getElementById('historyBackfillToggle');
const backfillFrom   = document.getElementById('historyBackfillFrom');
const backfillTo     = document.getElementById('historyBackfillTo');
const backfillPreview = document.getElementById('historyBackfillPreview');
const backfillRunBtn = document.getElementById('historyBackfillRunBtn');
const backfillClose  = document.getElementById('historyBackfillCloseBtn');

// Default the "To" date to today
backfillTo.value = new Date().toISOString().slice(0, 10);

function updateBackfillPreview() {
    const from = backfillFrom.value;
    const to   = backfillTo.value;
    if (from && to) {
        const days = Math.round(
            (new Date(to) - new Date(from)) / 86400000);
        backfillPreview.textContent = days >= 0
            ? `${days} day${days !== 1 ? 's' : ''}`
            : '⚠ End must be after start';
    } else {
        backfillPreview.textContent = '';
    }
}

backfillToggle.addEventListener('click', () => {
    const open = backfillPanel.style.display !== 'none';
    backfillPanel.style.display = open ? 'none' : 'block';
    backfillToggle.textContent = open ? '↩ Backfill' : '✕ Backfill';
    // Pre-fill "from" to last fetch date when opening
    if (!open && lastFetchDate && !backfillFrom.value) {
        backfillFrom.value = lastFetchDate;
        updateBackfillPreview();
    }
});

backfillClose.addEventListener('click', () => {
    backfillPanel.style.display = 'none';
    backfillToggle.textContent = '↩ Backfill';
});

backfillFrom.addEventListener('change', updateBackfillPreview);
backfillTo.addEventListener('change', updateBackfillPreview);

backfillRunBtn.addEventListener('click', () => {
    const from = backfillFrom.value;
    const to   = backfillTo.value;
    if (!from || !to) {
        backfillPreview.textContent = '⚠ Please choose both dates';
        return;
    }
    if (new Date(to) < new Date(from)) {
        backfillPreview.textContent = '⚠ End must be after start';
        return;
    }
    backfillPanel.style.display = 'none';
    backfillToggle.textContent = '↩ Backfill';
    runHistorySync(from, to);
});

// ── Load Cached (auto-called; no explicit button anymore) ─────────────
export async function loadCachedHistory(silent = false) {
    const status = document.getElementById('historyStatus');

    if (!silent) {
        status.textContent = 'Reading from local database…';
    }

    try {
        const data = await apiCall('/mainwp/update-history/cached');
        historyData = data.records || [];
        if (historyData.length) {
            if (!silent) {
                status.textContent = `${data.total_records} records loaded from local DB`;
            }
            renderHistory();
        } else if (!silent) {
            status.textContent = 'No cached records yet — click "Sync Updates" to fetch from MainWP.';
        }
    } catch (e) {
        if (!silent) status.textContent = `Error: ${e.message}`;
    }
}

// ── DB Stats bar ─────────────────────────────────────────────────────────
export async function loadDbStats() {
    try {
        const stats = await apiCall('/db/stats');
        const bar = document.getElementById('dbStatsBar');

        // Save last fetch date for incremental sync info + backfill pre-fill
        lastFetchDate = stats.last_fetch_date || null;

        // Update sync info label next to Sync button
        const syncInfo = document.getElementById('historySyncInfo');
        if (lastFetchDate) {
            const today = new Date().toISOString().slice(0, 10);
            syncInfo.textContent = `Will sync: ${lastFetchDate} → ${today}`;
        } else {
            syncInfo.textContent = 'No prior sync — will fetch last 30 days';
        }

        if (stats.total_records > 0) {
            bar.style.display = 'block';
            document.getElementById('dbStatTotal').textContent =
                stats.total_records.toLocaleString();
            document.getElementById('dbStatSites').textContent =
                stats.unique_sites;
            document.getElementById('dbStatOldest').textContent =
                stats.oldest_record
                    ? stats.oldest_record.slice(0, 10) : '—';
            const lf = stats.last_fetch;
            document.getElementById('dbStatFetch').textContent = lf
                ? `${lf.fetched_at.slice(0, 16).replace('T', ' ')} (+${lf.records_new} new)`
                : '—';
        }
    } catch (e) { /* silently skip */ }
}

// ── Filter bar wiring ─────────────────────────────────────────────────────

// Read the currently selected type pill's value
let _activeType = '';
// Map site name → site_id for the dropdown; populated by buildSiteDropdown()
let _siteIdMap = {};

function getActiveFilters() {
    return {
        type:     _activeType,
        site:     document.getElementById('filterSite').value,     // site name
        site_id:  _siteIdMap[document.getElementById('filterSite').value] || '',
        name:     document.getElementById('filterName').value.toLowerCase().trim(),
        dateFrom: document.getElementById('filterDateFrom').value, // YYYY-MM-DD
        dateTo:   document.getElementById('filterDateTo').value,
    };
}

// Populate site dropdown from loaded historyData
function buildSiteDropdown() {
    const sel = document.getElementById('filterSite');
    const current = sel.value;
    // Collect unique site names + map to id
    const siteMap = {};
    historyData.forEach(r => {
        const name = r._site_name || r.site_name || '';
        const id   = r._site_id   || r.site_id   || '';
        if (name) siteMap[name] = id;
    });
    _siteIdMap = siteMap;
    const names = Object.keys(siteMap).sort();
    sel.innerHTML = '<option value="">All sites</option>'
        + names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    if (names.includes(current)) sel.value = current;
}

// Type pill clicks
document.querySelectorAll('#historyFilterBar .pill').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#historyFilterBar .pill').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        _activeType = btn.dataset.type;
        renderHistory();
    });
});

document.getElementById('filterSite').addEventListener('change', renderHistory);
document.getElementById('filterName').addEventListener('input',  renderHistory);
document.getElementById('filterDateFrom').addEventListener('change', renderHistory);
document.getElementById('filterDateTo').addEventListener('change', renderHistory);

document.getElementById('filterClearBtn').addEventListener('click', () => {
    _activeType = '';
    document.querySelectorAll('#historyFilterBar .pill').forEach(p => p.classList.remove('active'));
    document.querySelector('#historyFilterBar .pill[data-type=""]').classList.add('active');
    document.getElementById('filterSite').value = '';
    document.getElementById('filterName').value = '';
    document.getElementById('filterDateFrom').value = '';
    document.getElementById('filterDateTo').value = '';
    renderHistory();
});

// ── Record normalisation + rendering ─────────────────────────────────────
// Normalize a record so field names work regardless of source.
// Live fetch uses _site_name/_update_type; DB uses site_name/update_type
// Exported — also used by the Site Dashboard's Updates section (app.js).
export function normalizeRecord(r) {
    return {
        ...r,
        _site_name:   r._site_name   || r.site_name   || '—',
        _site_url:    r._site_url     || r.site_url    || '',
        _update_type: r._update_type  || r.update_type || '',
        // DB stores new_version as new_version; live fetch uses current_version
        current_version: r.current_version || r.new_version || '',
    };
}

// ── Updates shared sort + row renderer ───────────────────────────────────
const _updTypeColors = { plugins: 'var(--accent)', themes: 'var(--yellow)', wordpress: 'var(--green)' };

function _sortUpdateRecords(records, col, dir) {
    return [...records].sort((a, b) => {
        let aVal, bVal;
        switch (col) {
            case 'site':        aVal = (a._site_name || '').toLowerCase(); bVal = (b._site_name || '').toLowerCase(); break;
            case 'type':        aVal = a._update_type || ''; bVal = b._update_type || ''; break;
            case 'name':        aVal = (a.name || a.title || a.slug || '').toLowerCase(); bVal = (b.name || b.title || b.slug || '').toLowerCase(); break;
            case 'old_version': aVal = a.old_version || ''; bVal = b.old_version || ''; break;
            case 'new_version': aVal = a.current_version || a.new_version || ''; bVal = b.current_version || b.new_version || ''; break;
            case 'date':
            default:
                return dir === 'asc' ? parseDateTs(a) - parseDateTs(b) : parseDateTs(b) - parseDateTs(a);
        }
        const cmp = String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
        return dir === 'asc' ? cmp : -cmp;
    });
}

// Renders sorted update rows into tbody. columns controls which cells are shown.
// Sort state is caller-owned so main-page and Site Dashboard can sort independently.
// Exported — also used by the Site Dashboard's Updates section (app.js).
export function renderUpdatesRowsInto({ tbody, records, columns, sortState }) {
    const sorted = _sortUpdateRecords(records, sortState.col, sortState.dir);
    tbody.innerHTML = sorted.map(rec => {
        const typeColor = _updTypeColors[rec._update_type] || 'var(--text-muted)';
        const name = rec.name || rec.title || rec.slug || '—';
        const cells = columns.map(col => {
            switch (col) {
                case 'site':        return `<td>${escapeHtml(rec._site_name || '—')}</td>`;
                case 'type':        return `<td><span style="color:${typeColor}; font-weight:500; text-transform:capitalize;">${rec._update_type || '—'}</span></td>`;
                case 'name':        return `<td>${escapeHtml(name)}</td>`;
                case 'old_version': return `<td style="color:var(--text-muted);">${escapeHtml(rec.old_version || '—')}</td>`;
                case 'new_version': return `<td style="color:var(--green);">${escapeHtml(rec.current_version || rec.new_version || '—')}</td>`;
                case 'date':        return `<td style="white-space:nowrap;">${escapeHtml(formatDateOnly(rec))}</td>`;
                default: return '<td>—</td>';
            }
        });
        return `<tr>${cells.join('')}</tr>`;
    }).join('');
}

// ── Column sort state ─────────────────────────────────────────────────────
let _sortCol = 'date';
let _sortDir = 'desc'; // 'asc' | 'desc'
let _filteredRecords = []; // always reflects what's currently shown in the table

// Wire sortable header clicks (elements exist above <script> tag)
document.querySelectorAll('#historyTable thead th.sortable').forEach(th => {
    th.addEventListener('click', () => {
        const col = th.dataset.col;
        if (_sortCol === col) {
            _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            _sortCol = col;
            _sortDir = col === 'date' ? 'desc' : 'asc';
        }
        // Update header classes + arrow glyphs
        document.querySelectorAll('#historyTable thead th.sortable').forEach(h => {
            h.classList.remove('sort-asc', 'sort-desc');
            h.querySelector('.sort-arrow').textContent = '⇅';
        });
        th.classList.add(_sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
        th.querySelector('.sort-arrow').textContent = _sortDir === 'asc' ? '↑' : '↓';
        renderHistory();
    });
});

// ── Date helpers ──────────────────────────────────────────────────────────
// updated_utime is "YYYY-MM-DD HH:MM:SS" — most reliable for sorting.
// updated_date is human-formatted and sometimes has a leading DOW.
function parseDateTs(rec) {
    if (rec.updated_utime) {
        const t = new Date(rec.updated_utime.replace(' ', 'T')).getTime();
        if (!isNaN(t)) return t;
    }
    // Strip leading DOW ("Thursday, ") before parsing updated_date
    const raw = (rec.updated_date || '').replace(/^[A-Za-z]+,\s*/, '');
    if (raw) {
        const t = new Date(raw).getTime();
        if (!isNaN(t)) return t;
    }
    return 0;
}

function formatDateOnly(rec) {
    const ts = parseDateTs(rec);
    if (!ts) return rec.updated_date || '—';
    return new Date(ts).toLocaleDateString('en-US',
        { month: 'long', day: 'numeric', year: 'numeric' });
}

function renderHistory() {
    const tbody  = document.getElementById('historyBody');
    const empty  = document.getElementById('historyEmpty');
    const bar    = document.getElementById('historyFilterBar');
    const countEl = document.getElementById('filterCount');

    let records = historyData.map(normalizeRecord);

    if (!records.length) {
        bar.style.display = 'none';
        tbody.innerHTML = '';
        empty.style.display = '';
        empty.textContent = 'No update records found. Click "Sync Updates" to fetch from MainWP.';
        countEl.textContent = '';
        return;
    }

    // Show filter bar and rebuild site dropdown
    bar.style.display = '';
    buildSiteDropdown();

    // Apply filters
    const f = getActiveFilters();
    if (f.type) {
        records = records.filter(r => r._update_type === f.type);
    }
    if (f.site) {
        records = records.filter(r => (r._site_name || '') === f.site);
    }
    if (f.name) {
        records = records.filter(r => {
            const n = (r.name || r.title || r.slug || '').toLowerCase();
            return n.includes(f.name);
        });
    }
    if (f.dateFrom) {
        const fromTs = new Date(f.dateFrom).getTime(); // YYYY-MM-DD → midnight
        records = records.filter(r => parseDateTs(r) >= fromTs);
    }
    if (f.dateTo) {
        // Include the entire "to" day by adding 1 day - 1ms
        const toTs = new Date(f.dateTo).getTime() + 86400000 - 1;
        records = records.filter(r => parseDateTs(r) <= toTs);
    }

    const total = historyData.length;
    countEl.textContent = records.length < total
        ? `${records.length.toLocaleString()} of ${total.toLocaleString()}`
        : `${total.toLocaleString()} records`;

    // Snapshot what's visible so export always matches the table
    _filteredRecords = records;

    if (!records.length) {
        tbody.innerHTML = '';
        empty.style.display = '';
        empty.textContent = 'No records match the current filters.';
        return;
    }
    empty.style.display = 'none';

    renderUpdatesRowsInto({
        tbody,
        records,
        columns: ['site', 'type', 'name', 'old_version', 'new_version', 'date'],
        sortState: { col: _sortCol, dir: _sortDir },
    });
}
