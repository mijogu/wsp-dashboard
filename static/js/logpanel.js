// ============================================================================
// Log Panel
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { escapeHtml } from './dom.js';
import { dbg, getClientDebugLog } from './debug.js';

dbg('module', 'logpanel.js loaded');

// Restore log panel state from localStorage
let _savedLogState = (() => {
    try { return JSON.parse(localStorage.getItem('wsp_log_panel') || '{}'); } catch { return {}; }
})();
let logPanelExpanded = _savedLogState.expanded !== undefined ? _savedLogState.expanded : true;
let lastLogCount = 0;
let _logCustomHeight = _savedLogState.height || null;

function _saveLogPanelState() {
    try {
        localStorage.setItem('wsp_log_panel', JSON.stringify({
            expanded: logPanelExpanded,
            height: _logCustomHeight,
        }));
    } catch { /* ignore */ }
}

export function toggleLogPanel() {
    const panel = document.getElementById('logPanel');
    logPanelExpanded = !logPanelExpanded;
    panel.classList.remove('resizing');
    if (logPanelExpanded) {
        // Restore drag-set height if the user had resized, else class default
        panel.className = 'log-panel expanded';
        if (_logCustomHeight !== null) panel.style.height = _logCustomHeight + 'px';
        else panel.style.height = '';
    } else {
        panel.style.height = '';
        panel.className = 'log-panel collapsed';
    }
    document.getElementById('logToggle').textContent = logPanelExpanded ? 'Collapse' : 'Expand';
    _saveLogPanelState();
}

export async function fetchLogs() {
    try {
        const resp = await fetch('/api/logs');
        if (!resp.ok) return;
        const logs = await resp.json();
        // Merge in client-side wiring/debug errors (e.g. a route handler
        // that threw, or a missing DOM element caught by auditWiring) so
        // they're visible in the same panel as server-side activity.
        renderLogs([...logs, ...getClientDebugLog()].sort((a, b) => (a.ts || 0) - (b.ts || 0)));
    } catch (e) {
        // silently fail — don't log about failing to fetch logs
    }
}

const LOG_TRUNCATE_LEN = 150;

export function renderLogs(logs) {
    const body = document.getElementById('logBody');
    const countEl = document.getElementById('logCount');
    const hasErrors = logs.some(l => l.level === 'error');

    countEl.textContent = logs.length;
    countEl.className = 'log-count' + (hasErrors ? ' has-errors' : '');

    let html = '';
    for (let i = 0; i < logs.length; i++) {
        const log = logs[i];
        const msg = log.message || '';
        const isLong = msg.length > LOG_TRUNCATE_LEN;

        let msgHtml;
        if (isLong) {
            const short = escapeHtml(msg.slice(0, LOG_TRUNCATE_LEN)) + '…';
            const full = escapeHtml(msg);
            // Try to pretty-print JSON
            let prettyFull = full;
            try {
                const jsonStart = msg.indexOf('{');
                const jsonEnd = msg.lastIndexOf('}');
                if (jsonStart >= 0 && jsonEnd > jsonStart) {
                    const prefix = escapeHtml(msg.slice(0, jsonStart));
                    const jsonStr = msg.slice(jsonStart, jsonEnd + 1);
                    const parsed = JSON.parse(jsonStr);
                    prettyFull = prefix + escapeHtml(JSON.stringify(parsed, null, 2));
                }
            } catch(e) { /* not JSON, use raw */ }

            msgHtml = `<span class="log-msg truncated" data-idx="${i}" onclick="this.classList.toggle('expanded')"><span class="short-text">${short}<span class="expand-hint">[expand]</span></span><span class="full-text">${prettyFull}</span></span>`;
        } else {
            msgHtml = `<span class="log-msg">${escapeHtml(msg)}</span>`;
        }

        html += `<div class="log-entry ${log.level}">
            <span class="log-time">${log.time}</span>
            <span class="log-source">${log.source}</span>
            ${msgHtml}
        </div>`;
        if (log.detail) {
            html += `<div class="log-detail">${escapeHtml(log.detail)}</div>`;
        }
    }
    body.innerHTML = html;

    // Auto-scroll to bottom if new entries
    if (logs.length > lastLogCount) {
        body.scrollTop = body.scrollHeight;
    }
    lastLogCount = logs.length;
}

// ── Log polling with mousedown-pause / mouseup-resume (5s delay) ────────
let _logPaused = false;
let _logResumeTimer = null;

// Wires up everything that needs the DOM fully parsed: log header click
// (was an inline onclick="toggleLogPanel()"), pause/resume on interaction,
// and drag-to-resize. Call once from app.js's DOMContentLoaded handler.
export function initLogPanel() {
    const _logBody = document.getElementById('logBody');
    const _logPausedBadge = document.getElementById('logPausedBadge');

    document.getElementById('logHeader').addEventListener('click', toggleLogPanel);

    _logBody.addEventListener('mousedown', () => {
        _logPaused = true;
        if (_logResumeTimer) { clearTimeout(_logResumeTimer); _logResumeTimer = null; }
        _logPausedBadge.style.display = '';
    });

    document.addEventListener('mouseup', () => {
        if (!_logPaused) return;
        if (_logResumeTimer) clearTimeout(_logResumeTimer);
        _logResumeTimer = setTimeout(() => {
            _logPaused = false;
            _logResumeTimer = null;
            _logPausedBadge.style.display = 'none';
            fetchLogs();
        }, 5000);
    });

    setInterval(() => { if (!_logPaused) fetchLogs(); }, 2000);

    // ── Log panel drag-to-resize ──────────────────────────────────────────
    const _logPanel  = document.getElementById('logPanel');
    const _logHandle = document.getElementById('logResizeHandle');
    const _container = document.querySelector('.container');
    const LOG_MIN_H  = 60;
    const LOG_MAX_H  = () => Math.floor(window.innerHeight * 0.85);

    let _dragStartY = null;
    let _dragStartH = null;

    _logHandle.addEventListener('mousedown', e => {
        if (!logPanelExpanded) return; // can't resize while collapsed
        e.preventDefault();
        _dragStartY = e.clientY;
        _dragStartH = _logPanel.offsetHeight;
        _logPanel.classList.add('resizing');
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', e => {
        if (_dragStartY === null) return;
        const delta = _dragStartY - e.clientY; // drag up → positive → taller
        const newH = Math.min(Math.max(_dragStartH + delta, LOG_MIN_H), LOG_MAX_H());
        _logPanel.style.height = newH + 'px';
        _logCustomHeight = newH;
        // Keep main content from hiding behind the panel
        if (_container) _container.style.paddingBottom = (newH + 20) + 'px';
    });

    document.addEventListener('mouseup', e => {
        if (_dragStartY === null) return;
        _dragStartY = null;
        _dragStartH = null;
        _logPanel.classList.remove('resizing');
        document.body.style.userSelect = '';
        _saveLogPanelState();
    });

    // Restore log panel state from localStorage
    if (!logPanelExpanded) {
        _logPanel.style.height = '';
        _logPanel.className = 'log-panel collapsed';
        document.getElementById('logToggle').textContent = 'Expand';
    } else if (_logCustomHeight !== null) {
        _logPanel.style.height = _logCustomHeight + 'px';
        if (_container) _container.style.paddingBottom = (_logCustomHeight + 20) + 'px';
    }
}
