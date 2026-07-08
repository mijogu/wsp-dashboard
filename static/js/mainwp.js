// ============================================================================
// Render: MainWP Tab
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. No route handler — populated together with the other
// preloaded tabs by loadAllData() in app.js.

import { appState } from './state.js';

import { dbg } from './debug.js';
dbg('module', 'mainwp.js loaded');

export async function renderMainWP() {
    const mwp = appState.data.mainwp;
    // Handle response formats: {data:[...]}, {result:[...]}, or [...]
    const sites = mwp ? (mwp.data || mwp.result || (Array.isArray(mwp) ? mwp : [])) : [];

    if (!mwp || sites.length === 0) {
        document.getElementById('mwpStatsRow').innerHTML = '';
        document.getElementById('coreUpdatesBody').innerHTML = '<tr><td colspan="4" style="text-align: center;">No data</td></tr>';
        document.getElementById('pluginUpdatesBody').innerHTML = '<tr><td colspan="3" style="text-align: center;">No data</td></tr>';
        document.getElementById('mwpSitesGrid').innerHTML = '<div class="empty-state"><h3>No sites</h3></div>';
        document.getElementById('allMwpSitesGrid').innerHTML = '<div class="empty-state"><h3>No sites</h3></div>';
        return;
    }

    const childSiteCount = sites.length;

    // Parse updates — handle multiple response formats
    const updatesRaw = appState.data.mainwpUpdates || {};
    const updatesData = updatesRaw.data || updatesRaw.result || updatesRaw;
    let updatesList = [];
    if (Array.isArray(updatesData)) {
        updatesList = updatesData;
    } else if (typeof updatesData === 'object') {
        // May be grouped: { wp: [...], plugins: [...], themes: [...] }
        // or { wordpress: [...], ... }
        const wp = updatesData.wp || updatesData.wordpress || [];
        const plugins = updatesData.plugins || [];
        const themes = updatesData.themes || [];
        const translations = updatesData.translations || [];
        updatesList = [
            ...wp.map(u => ({...u, _type: 'wp'})),
            ...plugins.map(u => ({...u, _type: 'plugin'})),
            ...themes.map(u => ({...u, _type: 'theme'})),
            ...translations.map(u => ({...u, _type: 'translation'})),
        ];
    }

    const wpCoreUpdates = updatesList.filter(u => (u._type || u.type) === 'wp' || (u._type || u.type) === 'wp_core' || (u._type || u.type) === 'wordpress');
    const pluginUpdates = updatesList.filter(u => (u._type || u.type) === 'plugin');
    const themeUpdates = updatesList.filter(u => (u._type || u.type) === 'theme');

    const pendingUpdatesCount = updatesList.length;

    // Stats
    const mwpStatsRow = document.getElementById('mwpStatsRow');
    mwpStatsRow.innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Child Sites</div>
            <div class="stat-value">${childSiteCount}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Pending Updates</div>
            <div class="stat-value ${pendingUpdatesCount > 0 ? 'yellow' : 'green'}">${pendingUpdatesCount}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">WP Core</div>
            <div class="stat-value ${wpCoreUpdates.length > 0 ? 'red' : 'green'}">${wpCoreUpdates.length}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Plugins</div>
            <div class="stat-value ${pluginUpdates.length > 10 ? 'yellow' : ''}">${pluginUpdates.length}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Themes</div>
            <div class="stat-value">${themeUpdates.length}</div>
        </div>
    `;

    // WP Core updates table
    const coreUpdatesBody = document.getElementById('coreUpdatesBody');
    if (wpCoreUpdates.length > 0) {
        coreUpdatesBody.innerHTML = wpCoreUpdates
            .map(u => {
                const siteName = u.site_name || u.name || u.site || 'Unknown';
                const current = u.current || u.old_version || u.version || '?';
                const available = u.new_version || u.update || '?';
                return `<tr>
                    <td style="font-weight:500">${siteName}</td>
                    <td style="color:var(--text-muted)">${current}</td>
                    <td style="color:var(--green)">${available}</td>
                    <td><span class="section-badge badge-red">Update</span></td>
                </tr>`;
            }).join('');
    } else {
        coreUpdatesBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color:var(--green)">All sites on latest WP core</td></tr>';
    }

    // ── Build per-site update breakdown ──
    // Group updates by site (try multiple field names MainWP might use)
    const siteUpdatesMap = {};
    updatesList.forEach(u => {
        const siteKey = u.site_name || u.site || u.website || 'Unknown';
        if (!siteUpdatesMap[siteKey]) siteUpdatesMap[siteKey] = { wp: [], plugins: [], themes: [] };
        const t = u._type || u.type || '';
        if (t === 'wp' || t === 'wp_core' || t === 'wordpress') siteUpdatesMap[siteKey].wp.push(u);
        else if (t === 'plugin') siteUpdatesMap[siteKey].plugins.push(u);
        else if (t === 'theme') siteUpdatesMap[siteKey].themes.push(u);
    });

    // Most common plugin updates
    const pluginNameCounts = {};
    pluginUpdates.forEach(u => {
        const name = u.name || u.plugin || u.slug || 'Unknown';
        pluginNameCounts[name] = (pluginNameCounts[name] || 0) + 1;
    });

    const pluginUpdatesBody = document.getElementById('pluginUpdatesBody');
    const topPlugins = Object.entries(pluginNameCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 15);

    if (topPlugins.length > 0) {
        pluginUpdatesBody.innerHTML = topPlugins
            .map(([name, count]) => `
            <tr>
                <td style="font-weight:500">${name}</td>
                <td>${count} site${count !== 1 ? 's' : ''}</td>
                <td><span class="duration-badge ${count > 5 ? 'dur-long' : 'dur-short'}">${count > 5 ? 'Widespread' : 'Available'}</span></td>
            </tr>`).join('');
    } else {
        pluginUpdatesBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color:var(--green)">All plugins up-to-date</td></tr>';
    }

    // ── Full updates-by-site report ──
    const mwpSitesGrid = document.getElementById('mwpSitesGrid');
    // Sort sites: those with updates first, then alphabetical
    const sitesWithUpdateInfo = sites.map(site => {
        const key = site.name || site.site_name || '';
        const updates = siteUpdatesMap[key] || { wp: [], plugins: [], themes: [] };
        const totalUpdates = updates.wp.length + updates.plugins.length + updates.themes.length;
        return { site, updates, totalUpdates };
    }).sort((a, b) => b.totalUpdates - a.totalUpdates || (a.site.name || '').localeCompare(b.site.name || ''));

    mwpSitesGrid.innerHTML = sitesWithUpdateInfo.map(({ site, updates, totalUpdates }) => {
        const hasCore = updates.wp.length > 0;
        const cardClass = hasCore ? 'status-down' : totalUpdates > 0 ? 'status-degraded' : 'status-ok';
        const pluginList = updates.plugins.map(p =>
            `<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:12px">
                <span>${p.name || p.plugin || p.slug || '?'}</span>
                <span style="color:var(--text-muted)">${p.old_version || p.version || '?'} → <span style="color:var(--green)">${p.new_version || p.update || '?'}</span></span>
            </div>`
        ).join('');
        const themeList = updates.themes.map(t =>
            `<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:12px">
                <span>${t.name || t.theme || t.slug || '?'}</span>
                <span style="color:var(--text-muted)">${t.old_version || t.version || '?'} → <span style="color:var(--green)">${t.new_version || t.update || '?'}</span></span>
            </div>`
        ).join('');

        return `
        <div class="site-card ${cardClass}">
            <div class="site-top">
                <div>
                    <div class="site-name">${site.name || 'Unknown'}</div>
                    <div class="site-url">${site.url || ''}</div>
                </div>
                <div style="font-size:12px;font-weight:600;color:${totalUpdates > 0 ? 'var(--yellow)' : 'var(--green)'}">
                    ${totalUpdates > 0 ? totalUpdates + ' update' + (totalUpdates !== 1 ? 's' : '') : 'Up to date'}
                </div>
            </div>
            ${hasCore ? `<div style="background:var(--red-bg);border-radius:6px;padding:6px 10px;margin-bottom:8px;font-size:12px;color:var(--red);font-weight:500">
                WP Core: ${updates.wp[0].old_version || updates.wp[0].version || '?'} → ${updates.wp[0].new_version || updates.wp[0].update || '?'}
            </div>` : ''}
            ${updates.plugins.length > 0 ? `<div style="margin-bottom:6px">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Plugins (${updates.plugins.length})</div>
                ${pluginList}
            </div>` : ''}
            ${updates.themes.length > 0 ? `<div>
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Themes (${updates.themes.length})</div>
                ${themeList}
            </div>` : ''}
            ${totalUpdates === 0 ? '<div style="font-size:12px;color:var(--green)">Everything current</div>' : ''}
        </div>`;
    }).join('');

    // All child sites grid — try many possible field names
    const allMwpSitesGrid = document.getElementById('allMwpSitesGrid');

    // Log first site's keys for debugging field names
    if (sites.length > 0) {
        console.log('MainWP site fields:', Object.keys(sites[0]));
        console.log('First site data:', JSON.stringify(sites[0]).slice(0, 500));
    }

    allMwpSitesGrid.innerHTML = sites
        .map(site => {
            const wpVer = site.wp_version || site.wpversion || site.version || site.wordpress_version || null;
            const phpVer = site.php_version || site.phpversion || null;
            const lastSync = site.last_sync || site.lastsync || site.last_post_gmt || null;
            const adminEmail = site.admin_email || site.email || null;
            const wpTheme = site.active_theme || site.theme || null;
            const sslStatus = site.ssl || site.is_ssl || null;

            // Build metrics dynamically — only show what we have
            let metrics = '';
            if (wpVer) metrics += `<div class="metric"><span class="metric-label">WP:</span><span class="metric-val">${wpVer}</span></div>`;
            if (phpVer) metrics += `<div class="metric"><span class="metric-label">PHP:</span><span class="metric-val">${phpVer}</span></div>`;
            if (wpTheme) metrics += `<div class="metric"><span class="metric-label">Theme:</span><span class="metric-val">${wpTheme}</span></div>`;
            if (adminEmail) metrics += `<div class="metric"><span class="metric-label">Email:</span><span class="metric-val">${adminEmail}</span></div>`;
            if (lastSync) {
                const syncDate = typeof lastSync === 'number' ? new Date(lastSync * 1000).toLocaleDateString() : lastSync;
                metrics += `<div class="metric"><span class="metric-label">Last sync:</span><span class="metric-val">${syncDate}</span></div>`;
            }
            if (!metrics) {
                // Show all keys so we can debug
                metrics = `<div class="metric"><span class="metric-label">Fields:</span><span class="metric-val" style="font-size:10px;color:var(--text-muted)">${Object.keys(site).join(', ')}</span></div>`;
            }

            return `
            <div class="site-card">
                <div class="site-top">
                    <div>
                        <div class="site-name">${site.name || site.site_name || 'Unknown'}</div>
                        <div class="site-url">${site.url || site.siteurl || ''}</div>
                    </div>
                </div>
                <div class="site-metrics">${metrics}</div>
            </div>`;
        }).join('');
}
