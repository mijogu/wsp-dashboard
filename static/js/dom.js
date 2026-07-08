import { dbg } from './debug.js';
dbg('module', 'dom.js loaded');

// ============================================================================
// DOM / formatting helpers — shared across feature modules
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. No dependencies on other modules.

// ── Modal Stack — ESC key support for all closeable modals ─────────────────
const _modalStack = [];

export function modalPush(id, closeFn) {
    modalRemove(id);
    _modalStack.push({ id, fn: closeFn });
}
export function modalRemove(id) {
    const idx = _modalStack.findIndex(m => m.id === id);
    if (idx !== -1) _modalStack.splice(idx, 1);
}

document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !_modalStack.length) return;
    e.preventDefault();
    _modalStack[_modalStack.length - 1].fn();
});

// ── HTML escaping ────────────────────────────────────────────────────────
export function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Duplicate manual-escape helper used by onboarding/checklists — kept as-is
// (not merged with escapeHtml) to avoid behavior changes during the split.
export function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

export function safeJsonParse(str, fallback) {
    if (!str) return fallback;
    if (Array.isArray(str)) return str;
    try { return JSON.parse(str); } catch { return fallback; }
}

export function showStatus(elementId, message, type) {
    const element = document.getElementById(elementId);
    element.textContent = message;
    element.className = `status-text ${type}`;
    element.style.display = 'block';
}

// ── Sortable table header ───────────────────────────────────────────────
// Shared by Regression and Link Checker's own-sort tables.
export function _sortTh(label, col, sort, style = '') {
    const active = sort.col === col;
    const arrow = active ? (sort.dir === 1 ? ' ▲' : ' ▼') : '';
    const bg = active ? 'background:var(--surface-2); ' : '';
    return `<th style="${bg}cursor:pointer; user-select:none; ${style}" data-sort-col="${col}">${label}${arrow}</th>`;
}

// ── CSV export ───────────────────────────────────────────────────────────
export function downloadCsv(rows, filename) {
    const lines = rows.map(row =>
        row.map(cell => {
            const s = String(cell ?? '');
            return (s.includes(',') || s.includes('"') || s.includes('\n'))
                ? `"${s.replace(/"/g, '""')}"` : s;
        }).join(',')
    );
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
}
