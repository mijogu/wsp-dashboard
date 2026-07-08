// ============================================================================
// Router — hash-based URL routing
// URL scheme: #/{tab}  |  #/{tab}/{subview}  |  #/{tab}/{subview}/{id}
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.
//
// Deliberately decoupled from feature modules: rather than importing every
// feature's loader function directly (which would create import cycles once
// those loaders live in their own modules), the router exposes a small
// registry. Each feature calls registerRoute('tab', handlerFn) once at load
// time; _applyRoute looks the handler up by tab name. This is also what
// makes the wiring audit possible — auditWiring() can ask "does every tab
// that needs one have a registered handler?" without the router needing to
// know anything about the features themselves.

import { appState } from './state.js';
import { dbg, dbgError, auditWiring } from './debug.js';

dbg('module', 'router.js loaded');

const _SUBVIEW_TABS = ['cloudflare', 'regression', 'linkchecker'];
const _SUBVIEW_DEFAULTS = { cloudflare: 'analytics', regression: 'sites', linkchecker: 'sites' };

// Tabs whose content is driven by a registered route handler (as opposed to
// overview/uptime/mainwp, which are populated together by loadAllData and
// intentionally have no per-tab handler).
const TABS_REQUIRING_ROUTE = [
    'cloudflare', 'regression', 'linkchecker', 'sites',
    'history', 'settings', 'onboarding', 'checklists',
];

const _routes = {}; // tab -> (subview, id) => void

export function registerRoute(tab, fn) {
    _routes[tab] = fn;
}

export function hasRoute(tab) {
    return typeof _routes[tab] === 'function';
}

// Run once after all features have registered their routes (see app.js).
export function checkWiring() {
    return auditWiring({ requireRouteFor: TABS_REQUIRING_ROUTE, hasRoute });
}

// Exported under their original names (with leading underscore) so feature
// code that hasn't been peeled out of app.js yet — the sub-view appliers —
// can import them unchanged.
export function _getLastSubview(tab) {
    try { return JSON.parse(localStorage.getItem('wsp_last_subview') || '{}')[tab] || null; }
    catch { return null; }
}
export function _saveLastSubview(tab, subview) {
    try {
        const s = JSON.parse(localStorage.getItem('wsp_last_subview') || '{}');
        s[tab] = subview;
        localStorage.setItem('wsp_last_subview', JSON.stringify(s));
    } catch {}
}

export function parseHash() {
    const raw = location.hash.replace(/^#\/?/, '') || 'overview';
    const parts = raw.split('/');
    return { tab: parts[0] || 'overview', subview: parts[1] || null, id: parts[2] || null };
}
export function buildHash({ tab, subview, id } = {}) {
    let h = '#/' + (tab || 'overview');
    if (subview) h += '/' + subview;
    if (id != null) h += '/' + id;
    return h;
}

export function navigate({ tab, subview, id } = {}) {
    const hash = buildHash({ tab: tab || 'overview', subview, id });
    if (location.hash === hash) {
        _applyRoute(parseHash());
    } else {
        location.hash = hash; // triggers hashchange → _applyRoute
    }
}

export function applyRouteFromHash() { _applyRoute(parseHash()); }

function _applyRoute({ tab, subview, id }) {
    if (!appState.unlocked) return;
    // Expand bare sub-view tabs to last-used or default
    if (!subview && _SUBVIEW_TABS.includes(tab)) {
        subview = _getLastSubview(tab) || _SUBVIEW_DEFAULTS[tab];
        history.replaceState(null, '', buildHash({ tab, subview }));
    }
    switchTab(tab);
    dbg('route', tab, subview, id);
    const handler = _routes[tab];
    if (!handler) return; // overview/uptime/mainwp: no per-tab handler needed
    try {
        handler(subview, id);
    } catch (err) {
        dbgError('route', `handler for tab "${tab}" threw`, err);
    }
}

window.addEventListener('hashchange', applyRouteFromHash);

// ============================================================================
// Tab Navigation
// ============================================================================
document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
        navigate({ tab: tab.dataset.tab });
    });
});

// Pure DOM tab switcher — data loading is handled by _applyRoute
export function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach((c) => c.classList.remove('active'));
    document.getElementById('sitesBtn').classList.remove('active');
    document.getElementById('settingsBtn').classList.remove('active');
    document.getElementById('onboardingBtn').classList.remove('active');
    document.getElementById('checklistsBtn').classList.remove('active');

    const tabBarBtn = document.querySelector(`[data-tab="${tabName}"]`);
    if (tabBarBtn) {
        tabBarBtn.classList.add('active');
    } else if (tabName === 'sites') {
        document.getElementById('sitesBtn').classList.add('active');
    } else if (tabName === 'settings') {
        document.getElementById('settingsBtn').classList.add('active');
    } else if (tabName === 'onboarding') {
        document.getElementById('onboardingBtn').classList.add('active');
    } else if (tabName === 'checklists') {
        document.getElementById('checklistsBtn').classList.add('active');
    }

    document.getElementById(tabName).classList.add('active');
    appState.currentTab = tabName;
}
