// ============================================================================
// Render: Overview Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. No route handler — populated together with the other
// preloaded tabs by loadAllData() in app.js.

import { appState } from './state.js';

import { dbg } from './debug.js';
dbg('module', 'overview.js loaded');

export function renderOverview() {
    const ur = appState.data.uptimeRobot;
    if (!ur || !ur.monitors) {
        document.getElementById('statsRow').innerHTML = '<div class="empty-state"><h3>No data</h3></div>';
        return;
    }

    const monitors = ur.monitors || [];
    const totalMonitors = monitors.length;
    const upMonitors = monitors.filter((m) => m.status === 2).length;
    const downMonitors = monitors.filter((m) => m.status === 9).length;

    const logs = ur.logs || [];
    const responseTimes = logs
        .filter((l) => l.response_time)
        .map((l) => l.response_time);
    const avgResponseTime =
        responseTimes.length > 0
            ? Math.round(responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length)
            : 0;

    const responseTimes30d = ur.response_times
        ? ur.response_times.filter((r) => r.response_time).map((r) => r.response_time)
        : [];
    const avgUptime30d =
        ur.custom_uptime_ratios && ur.custom_uptime_ratios['30d']
            ? parseFloat(ur.custom_uptime_ratios['30d']).toFixed(2)
            : 'N/A';

    const cfZoneCount = appState.data.cloudflare?.result?.length ?? 0;

    const statsRow = document.getElementById('statsRow');
    statsRow.innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Total Monitors</div>
            <div class="stat-value">${totalMonitors}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Up</div>
            <div class="stat-value green">${upMonitors}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Down</div>
            <div class="stat-value red">${downMonitors}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Avg Response Time</div>
            <div class="stat-value">${avgResponseTime}ms</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Avg Uptime (30d)</div>
            <div class="stat-value green">${avgUptime30d}%</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">CF Zones</div>
            <div class="stat-value">${cfZoneCount}</div>
        </div>
    `;

    // Alert banner
    const alerts = [];
    downMonitors > 0 && alerts.push(`${downMonitors} site${downMonitors === 1 ? '' : 's'} down`);
    const slowSites = monitors.filter((m) => {
        const lastLog = (ur.logs || []).find((l) => l.monitor_id === m.id);
        return lastLog && lastLog.response_time > 2000;
    }).length;
    slowSites > 0 && alerts.push(`${slowSites} site${slowSites === 1 ? '' : 's'} slow (>2s)`);

    const alertBanner = document.getElementById('alertBanner');
    if (alerts.length > 0) {
        document.getElementById('alertItems').innerHTML = alerts
            .map((alert) => `<div class="alert-item">⚠️ ${alert}</div>`)
            .join('');
        alertBanner.classList.add('visible');
    } else {
        alertBanner.classList.remove('visible');
    }

    // Sites grid
    const overviewGrid = document.getElementById('overviewGrid');
    overviewGrid.innerHTML = monitors
        .map((monitor) => {
            const statusClass = {
                2: 'status-ok',
                9: 'status-down',
                1: 'status-paused',
            }[monitor.status] || 'status-ok';

            const statusDot =
                {
                    2: '<span class="status-dot up"></span>',
                    9: '<span class="status-dot down"></span>',
                    1: '<span class="status-dot paused"></span>',
                }[monitor.status] || '';

            const lastLog = (ur.logs || []).find((l) => l.monitor_id === monitor.id);
            const responseTime = lastLog ? lastLog.response_time : 0;

            const lastLogTime = lastLog && lastLog.datetime ? new Date(lastLog.datetime * 1000).toLocaleString() : 'N/A';

            const uptime30d = ur.custom_uptime_ratios ? Object.entries(ur.custom_uptime_ratios).find(([k]) => k.includes('30d'))?.[1] || 'N/A' : 'N/A';

            return `
                <div class="site-card ${statusClass}">
                    <div class="site-top">
                        <div>
                            <div class="site-name">${monitor.friendly_name}</div>
                            <div class="site-url">${monitor.url}</div>
                        </div>
                        ${statusDot}
                    </div>
                    <div class="site-metrics">
                        <div class="metric">
                            <span class="metric-label">Response:</span>
                            <span class="metric-val">${responseTime}ms</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Uptime (30d):</span>
                            <span class="metric-val">${uptime30d}%</span>
                        </div>
                    </div>
                    <div class="rt-bar-wrap">
                        <div class="rt-bar ${responseTime < 500 ? 'fast' : responseTime < 2000 ? 'medium' : 'slow'}" style="width: ${Math.min(100, (responseTime / 3000) * 100)}%"></div>
                    </div>
                </div>
            `;
        })
        .join('');
}
