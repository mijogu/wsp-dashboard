// ============================================================================
// Debug / wiring-audit logging
// ============================================================================
// New during the ES-module split. Purpose: catch pages/tabs that aren't
// wired up correctly (missing route handler, missing DOM element, uncaught
// error in a route handler, module that failed to import) — the failure
// mode this split is most at risk of introducing.
//
// Off by default (no console noise in normal use). Enable with either:
//   http://localhost:9111/?debug
//   localStorage.setItem('wsp_debug', '1')

export const DEBUG =
    new URLSearchParams(location.search).has('debug') ||
    localStorage.getItem('wsp_debug') === '1';

const MAX_CLIENT_LOG = 100;
const _clientLog = [];

// Verbose trace — only when DEBUG is on. Use for module-load and
// route-dispatch tracing.
export function dbg(scope, ...args) {
    if (DEBUG) console.debug(`[${scope}]`, ...args);
}

// Always logs to console, and always records into the client-side debug
// buffer so it can be surfaced in the on-screen log panel (see logpanel.js)
// even when DEBUG tracing is off — wiring failures shouldn't be silent.
export function dbgError(scope, message, err) {
    const detail = err ? (err.stack || err.message || String(err)) : undefined;
    console.error(`[${scope}] ${message}`, err || '');
    _clientLog.push({
        ts: Date.now(),
        time: new Date().toLocaleTimeString(),
        source: `client:${scope}`,
        level: 'error',
        message,
        detail,
    });
    if (_clientLog.length > MAX_CLIENT_LOG) _clientLog.shift();
}

export function getClientDebugLog() {
    return _clientLog;
}

// Generic wiring check: every `.tab[data-tab]` button must have a matching
// tab-content element, every expected special nav button must exist, and
// every tab in `requireRouteFor` must have a registered route handler
// (checked via the caller-supplied `hasRoute` predicate — kept generic so
// debug.js doesn't need to import the router's registry directly).
export function auditWiring({ requireRouteFor = [], hasRoute = () => true } = {}) {
    const problems = [];

    document.querySelectorAll('.tab[data-tab]').forEach(btn => {
        const tab = btn.dataset.tab;
        if (!document.getElementById(tab)) {
            problems.push(`tab "${tab}" has no matching #${tab} content element`);
        }
    });

    ['sitesBtn', 'settingsBtn', 'onboardingBtn', 'checklistsBtn'].forEach(id => {
        if (!document.getElementById(id)) {
            problems.push(`missing nav button #${id}`);
        }
    });

    requireRouteFor.forEach(tab => {
        if (!hasRoute(tab)) {
            problems.push(`tab "${tab}" has no registered route handler`);
        }
    });

    if (problems.length) {
        problems.forEach(p => dbgError('wiring', p));
    } else {
        dbg('wiring', 'auditWiring: all tabs wired correctly');
    }
    return problems;
}
