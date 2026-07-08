// ============================================================================
// API Calls
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { CONFIG } from './state.js';

import { dbg } from './debug.js';
dbg('module', 'api.js loaded');

export async function apiCall(endpoint, methodOrOptions = {}, body = undefined) {
    let options;
    if (typeof methodOrOptions === 'string') {
        options = { method: methodOrOptions };
        if (body !== undefined) options.body = JSON.stringify(body);
    } else {
        options = methodOrOptions;
    }
    try {
        const response = await fetch(`${CONFIG.API_BASE}api${endpoint}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        return data;
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        throw error;
    }
}

export async function getStatus() {
    return apiCall('/status');
}

export async function unlock(passphrase, remember = false) {
    return apiCall('/unlock', {
        method: 'POST',
        body: JSON.stringify({ passphrase, remember }),
    });
}

export async function getSettings() {
    return apiCall('/settings');
}

export async function saveSettings(settings) {
    return apiCall('/settings', {
        method: 'POST',
        body: JSON.stringify({ settings }),
    });
}

export async function getUptimeRobot() {
    return apiCall('/uptime-robot');
}

export async function getCloudflare() {
    return apiCall('/cloudflare/zones');
}

export async function getCloudflareAnalytics(zoneId, range = '24h') {
    return apiCall(`/cloudflare/analytics/${zoneId}?range=${range}`);
}

export async function getCloudflareZoneSettings(zoneId) {
    return apiCall(`/cloudflare/zone-settings/${zoneId}`);
}

export async function getMainWP() {
    return apiCall('/mainwp/sites');
}

export async function getMainWPUpdates() {
    return apiCall('/mainwp/updates');
}

export async function exportData() {
    return apiCall('/export');
}

export async function importData(data) {
    return apiCall('/import', {
        method: 'POST',
        body: JSON.stringify({ data }),
    });
}
