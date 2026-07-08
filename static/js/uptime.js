// ============================================================================
// Render: Uptime Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. No route handler — populated together with the other
// preloaded tabs by loadAllData() in app.js.

import { appState } from './state.js';

import { dbg } from './debug.js';
dbg('module', 'uptime.js loaded');

export function renderUptime() {
    const ur = appState.data.uptimeRobot;
    if (!ur || !ur.monitors) {
        document.getElementById('uptimeGrid').innerHTML = '<div class="empty-state"><h3>No monitors</h3></div>';
        return;
    }

    const uptimeGrid = document.getElementById('uptimeGrid');
    uptimeGrid.innerHTML = (ur.monitors || [])
        .map((monitor) => {
            const uptime7d = ur.custom_uptime_ratios ? Object.entries(ur.custom_uptime_ratios).find(([k]) => k.includes('7d'))?.[1] || 'N/A' : 'N/A';
            const uptime30d = ur.custom_uptime_ratios ? Object.entries(ur.custom_uptime_ratios).find(([k]) => k.includes('30d'))?.[1] || 'N/A' : 'N/A';

            const lastLog = (ur.logs || []).find((l) => l.monitor_id === monitor.id);
            const responseTime = lastLog ? lastLog.response_time : 0;

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
                            <span class="metric-label">7d Uptime:</span>
                            <span class="metric-val">${uptime7d}%</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">30d Uptime:</span>
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
