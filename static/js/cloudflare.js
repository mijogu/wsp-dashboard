// ============================================================================
// Render: Cloudflare Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { appState } from './state.js';
import { getCloudflareAnalytics, getCloudflareZoneSettings } from './api.js';
import { escapeHtml } from './dom.js';
import { buildHash, _saveLastSubview, registerRoute } from './router.js';

import { dbg } from './debug.js';
dbg('module', 'cloudflare.js loaded');

// CF sub-view — activates the right sub-tab button and pane
function _setCfSubview(subview) {
    const t = subview || 'analytics';
    document.querySelectorAll('.cf-sub-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.cfTab === t));
    document.getElementById('cfAnalyticsPane').style.display = t === 'analytics' ? '' : 'none';
    document.getElementById('cfSettingsPane').style.display  = t === 'settings'  ? '' : 'none';
    if (t === 'settings' && appState.data.cloudflare?.result) renderCfSettings();
}

registerRoute('cloudflare', (subview) => _setCfSubview(subview));

// CF sub-tab switching
document.querySelectorAll('.cf-sub-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.cfTab;
        history.replaceState(null, '', buildHash({ tab: 'cloudflare', subview: tab }));
        _saveLastSubview('cloudflare', tab);
        _setCfSubview(tab);
    });
});

document.getElementById('cfRangeSelect').addEventListener('change', () => renderCloudflare());

export async function renderCloudflare() {
    const cf = appState.data.cloudflare;
    if (!cf || !cf.result) {
        document.getElementById('cfStatsRow').innerHTML = '';
        document.getElementById('cfGrid').innerHTML = '<div class="empty-state"><h3>No zones — configure your Cloudflare API token in Settings.</h3></div>';
        return;
    }

    const zones = cf.result || [];
    const range = document.getElementById('cfRangeSelect').value;
    const rangeLabel = { '24h': '24h', '7d': '7 days', '30d': '30 days' }[range] || range;

    const cfGrid = document.getElementById('cfGrid');
    cfGrid.innerHTML = '<div style="padding:12px; color:var(--text-muted); font-size:13px;">Loading analytics…</div>';

    // Fetch all zone analytics in parallel
    const analyticsResults = await Promise.all(
        zones.map(z => getCloudflareAnalytics(z.id, range).catch(() => null))
    );

    let totalRequests = 0, totalBandwidth = 0, totalThreats = 0;
    cfGrid.innerHTML = '';

    zones.forEach((zone, i) => {
        const a = analyticsResults[i];
        const totals    = a?.result?.totals || {};
        const requests  = totals.requests?.all  || 0;
        const bandwidth = totals.bandwidth?.all || 0;
        const threats   = totals.threats?.all   || 0;
        const cached    = totals.requests?.cached || 0;
        const cacheRatio = requests > 0 ? (cached / requests) : 0;
        const uniques   = totals.uniques?.all   || 0;

        totalRequests  += requests;
        totalBandwidth += bandwidth;
        totalThreats   += threats;

        cfGrid.innerHTML += `
            <div class="cf-card">
                <h4>${zone.name}</h4>
                <div class="cf-stat-row">
                    <span class="label">Requests (${rangeLabel})</span>
                    <span>${requests.toLocaleString()}</span>
                </div>
                <div class="cf-stat-row">
                    <span class="label">Unique Visitors</span>
                    <span>${uniques.toLocaleString()}</span>
                </div>
                <div class="cf-stat-row">
                    <span class="label">Cache Ratio</span>
                    <span>${(cacheRatio * 100).toFixed(1)}%</span>
                </div>
                <div class="cf-stat-row">
                    <span class="label">Bandwidth</span>
                    <span>${(bandwidth / 1024 / 1024).toFixed(1)} MB</span>
                </div>
                <div class="cf-stat-row">
                    <span class="label">Threats</span>
                    <span${threats > 0 ? ' style="color:var(--red,#f87171)"' : ''}>${threats.toLocaleString()}</span>
                </div>
                <div class="cf-stat-row">
                    <span class="label">Plan</span>
                    <span>${zone.plan?.name || 'N/A'}</span>
                </div>
            </div>
        `;
    });

    document.getElementById('cfStatsRow').innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Total Requests (${rangeLabel})</div>
            <div class="stat-value">${totalRequests.toLocaleString()}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Bandwidth</div>
            <div class="stat-value">${(totalBandwidth / 1024 / 1024).toFixed(1)} MB</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Threats</div>
            <div class="stat-value${totalThreats > 0 ? ' red' : ''}">${totalThreats.toLocaleString()}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Zones</div>
            <div class="stat-value">${zones.length}</div>
        </div>
    `;
}

// CF Settings Comparison
const CF_SETTINGS_KEYS = [
    { id: 'ssl',                       label: 'SSL Mode' },
    { id: 'security_level',            label: 'Security Level' },
    { id: 'always_use_https',          label: 'Always HTTPS' },
    { id: 'min_tls_version',           label: 'Min TLS' },
    { id: 'tls_1_3',                   label: 'TLS 1.3' },
    { id: 'automatic_https_rewrites',  label: 'Auto HTTPS Rewrites' },
    { id: 'browser_check',             label: 'Browser Integrity' },
    { id: 'brotli',                    label: 'Brotli' },
    { id: 'http2',                     label: 'HTTP/2' },
    { id: 'http3',                     label: 'HTTP/3' },
    { id: '0rtt',                      label: '0-RTT' },
    { id: 'email_obfuscation',         label: 'Email Obfuscation' },
    { id: 'hotlink_protection',        label: 'Hotlink Protection' },
    { id: 'rocket_loader',             label: 'Rocket Loader' },
    { id: 'development_mode',          label: 'Dev Mode' },
];

let _cfSettingsSortKey = null;
let _cfSettingsSortDir = 1;

export async function renderCfSettings() {
    const zones = appState.data.cloudflare?.result || [];
    const el = document.getElementById('cfSettingsContent');
    el.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:13px;">Loading settings…</div>';

    const settingsResults = await Promise.all(
        zones.map(z => getCloudflareZoneSettings(z.id).catch(() => null))
    );

    const zoneSettings = settingsResults.map(r => {
        const map = {};
        for (const s of (r?.result || [])) map[s.id] = s.value;
        return map;
    });

    function majority(vals) {
        const freq = {};
        for (const v of vals) freq[String(v)] = (freq[String(v)] || 0) + 1;
        return Object.entries(freq).sort((a,b) => b[1]-a[1])[0]?.[0];
    }

    function cfValClass(key, val) {
        const s = String(val).toLowerCase();
        if (key === 'development_mode' && s === 'on') return 'cf-val-devmode';
        if (s === 'on' || s === 'full' || s === 'strict' || s === 'high') return 'cf-val-on';
        if (s === 'flexible') return 'cf-val-flexible';
        if (s === 'off') return 'cf-val-off';
        return '';
    }

    const relevantKeys = CF_SETTINGS_KEYS.filter(k =>
        zoneSettings.some(s => s[k.id] !== undefined)
    );

    if (!relevantKeys.length) {
        el.innerHTML = '<div class="empty-state"><h3>No settings data</h3><p>Ensure your API token has Zone Settings Read permission.</p></div>';
        return;
    }

    // Pre-compute majority value per setting column
    const majorityByKey = {};
    for (const key of relevantKeys) {
        const vals = zoneSettings.map(s => String(s[key.id] ?? '—')).filter(v => v !== '—');
        majorityByKey[key.id] = majority(vals);
    }

    // Sort zones
    let rows = zones.map((z, i) => ({ zone: z, settings: zoneSettings[i] }));
    if (_cfSettingsSortKey) {
        rows.sort((a, b) => {
            const va = String(a.settings[_cfSettingsSortKey] ?? '');
            const vb = String(b.settings[_cfSettingsSortKey] ?? '');
            return va.localeCompare(vb) * _cfSettingsSortDir;
        });
    }

    const arrow = (key) => key === _cfSettingsSortKey ? (_cfSettingsSortDir > 0 ? ' ▲' : ' ▼') : '';

    const thead = `<tr>
        <th>Site</th>
        ${relevantKeys.map(k =>
            `<th class="sortable" data-sort-key="${k.id}">${k.label}${arrow(k.id)}</th>`
        ).join('')}
    </tr>`;

    const tbody = rows.map(({ zone, settings }) => {
        const cells = relevantKeys.map(key => {
            const v = settings[key.id] ?? '—';
            const cls = cfValClass(key.id, String(v));
            const outlier = String(v) !== '—' && String(v) !== majorityByKey[key.id] ? ' cf-val-outlier' : '';
            return `<td class="${cls}${outlier}">${v}</td>`;
        }).join('');
        return `<tr><td><strong>${escapeHtml(zone.name)}</strong></td>${cells}</tr>`;
    }).join('');

    el.innerHTML = `
        <p style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">
            Yellow outline = value differs from majority. Click column headers to sort.
        </p>
        <div class="cf-settings-wrap">
            <table class="cf-settings-table">
                <thead>${thead}</thead>
                <tbody>${tbody}</tbody>
            </table>
        </div>`;

    el.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sortKey;
            if (_cfSettingsSortKey === key) {
                _cfSettingsSortDir *= -1;
            } else {
                _cfSettingsSortKey = key;
                _cfSettingsSortDir = 1;
            }
            renderCfSettings();
        });
    });
}
