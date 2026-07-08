import { dbg } from './debug.js';
dbg('module', 'state.js loaded');

// ============================================================================
// State & Configuration
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. Holds the single global app-state object and constants
// shared across every feature module.

export let appState = {
    unlocked: false,
    hasConfig: false,
    currentTab: 'overview',
    autoRefreshTimer: null,
    lastRefreshTime: null,
    data: {
        uptimeRobot: null,
        cloudflare: null,
        mainwp: null,
    },
};

export const CONFIG = {
    API_BASE: '/',
    REFRESH_INTERVAL_KEY: 'dashboardRefreshInterval',
    DEFAULT_REFRESH_INTERVAL: 300000,
};
