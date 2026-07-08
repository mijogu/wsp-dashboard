// ============================================================================
// Onboarding — field grid + Site Dashboard onboarding panel
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split. Two closely related sub-features that share this file:
// the main Onboarding tab's field grid, and the read/write onboarding panel
// embedded in each site's Site Dashboard.

import { modalPush, modalRemove, _esc } from './dom.js';
import { apiCall } from './api.js';
import { registerRoute } from './router.js';
import { _sdSiteId, registerSdOnboardingLoader } from './sites.js';

import { dbg } from './debug.js';
dbg('module', 'onboarding.js loaded');

        // Onboarding
        // ============================================================================

        let _obData = null; // { fields, sites, data }
        let _obCollapsedGroups = new Set();

        async function loadOnboarding() {
            document.getElementById('obTableContainer').innerHTML =
                '<div style="padding:16px;color:var(--text-muted);font-size:13px;">Loading…</div>';
            try {
                _obData = await apiCall('/onboarding/data');
                renderOnboarding();
            } catch(e) {
                document.getElementById('obTableContainer').innerHTML =
                    `<div class="empty-state"><h3>Error loading onboarding data</h3><p>${e.message}</p></div>`;
            }
        }

        function renderOnboarding() {
            if (!_obData) return;
            const { fields, sites, data } = _obData;
            const visibleFields = fields.filter(f => !f.hidden);

            if (!sites.length) {
                document.getElementById('obTableContainer').innerHTML =
                    '<div class="empty-state"><h3>No sites</h3><p>Add sites via MainWP and sync.</p></div>';
                return;
            }

            // Build group structure
            const groups = [];
            const groupMap = {};
            for (const f of visibleFields) {
                if (!groupMap[f.group_name]) {
                    groupMap[f.group_name] = [];
                    groups.push(f.group_name);
                }
                groupMap[f.group_name].push(f);
            }

            // Group header row (spans)
            let groupHeaderCells = '<th class="ob-sticky-num ob-col-header"></th><th class="ob-sticky-name ob-col-header">Site</th>';
            for (const g of groups) {
                const cols = groupMap[g].length;
                const collapsed = _obCollapsedGroups.has(g);
                groupHeaderCells += `<th colspan="${collapsed ? 1 : cols}" data-group="${g}" title="Click to ${collapsed ? 'expand' : 'collapse'}">${g}${collapsed ? ' ▶' : ' ▼'}</th>`;
            }

            // Field label row
            let fieldHeaderCells = '<th class="ob-sticky-num ob-col-header">#</th><th class="ob-sticky-name ob-col-header">Site</th>';
            for (const g of groups) {
                if (_obCollapsedGroups.has(g)) {
                    fieldHeaderCells += `<th class="ob-col-header" style="color:var(--text-muted);font-size:10px;">…</th>`;
                } else {
                    for (const f of groupMap[g]) {
                        fieldHeaderCells += `<th class="ob-col-header" title="${f.name}">${f.name}</th>`;
                    }
                }
            }

            // Body rows
            let bodyRows = '';
            sites.forEach((site, idx) => {
                const sid = String(site.id);
                const siteData = data[sid] || {};
                const removedCls = site.is_removed ? ' ob-removed' : '';
                let cells = `<td class="ob-sticky-num${removedCls}">${idx + 1}</td>`;
                cells += `<td class="ob-sticky-name${removedCls}" title="${site.url || ''}">${site.name}</td>`;
                for (const g of groups) {
                    if (_obCollapsedGroups.has(g)) {
                        cells += `<td style="background:var(--surface-2);"></td>`;
                    } else {
                        for (const f of groupMap[g]) {
                            cells += obCellHtml(site, f, siteData[f.id] ?? f.default_value ?? '');
                        }
                    }
                }
                bodyRows += `<tr data-site-id="${sid}">${cells}</tr>`;
            });

            document.getElementById('obTableContainer').innerHTML = `
                <table class="ob-table">
                    <thead>
                        <tr class="ob-group-header">${groupHeaderCells}</tr>
                        <tr>${fieldHeaderCells}</tr>
                    </thead>
                    <tbody>${bodyRows}</tbody>
                </table>`;

            // Set row-2 sticky top to match actual row-1 height
            const groupRow = document.querySelector('#obTableContainer .ob-group-header');
            if (groupRow) {
                const h = groupRow.offsetHeight + 'px';
                document.querySelectorAll('#obTableContainer .ob-col-header').forEach(th => th.style.top = h);
            }

            // Apply locked state (editing off by default; survives re-renders)
            if (!document.getElementById('obEditCheck').checked) {
                document.getElementById('obTableContainer').classList.add('ob-locked');
            }

            // Group collapse toggle
            document.querySelectorAll('.ob-group-header th[data-group]').forEach(th => {
                th.addEventListener('click', () => {
                    const g = th.dataset.group;
                    if (_obCollapsedGroups.has(g)) _obCollapsedGroups.delete(g);
                    else _obCollapsedGroups.add(g);
                    renderOnboarding();
                });
            });

            // Cell editing
            document.querySelectorAll('#obTableContainer .ob-cell').forEach(td => {
                const input = td.querySelector('input[type="text"], input[type="url"]');
                if (!input) return;
                input.addEventListener('blur', () => obSaveCell(td.dataset.siteId, td.dataset.fieldId, input.value));
                input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
            });

            document.querySelectorAll('#obTableContainer .ob-cell-bool').forEach(td => {
                td.addEventListener('click', () => {
                    const current = td.dataset.value === '1' ? '1' : '0';
                    const next = current === '1' ? '0' : '1';
                    td.dataset.value = next;
                    td.innerHTML = next === '1'
                        ? '<span class="ob-bool-on">✓</span>'
                        : '<span class="ob-bool-off">✓</span>';
                    obSaveCell(td.dataset.siteId, td.dataset.fieldId, next);
                });
            });
        }

        function obCellHtml(site, field, value) {
            const sid = site.id;
            const fid = field.id;
            if (field.field_type === 'bool') {
                const on = value === '1' || value === 'true' || value === 'yes';
                return `<td class="ob-cell-bool" data-site-id="${sid}" data-field-id="${fid}" data-value="${on ? '1' : '0'}">
                    <span class="${on ? 'ob-bool-on' : 'ob-bool-off'}">✓</span>
                </td>`;
            }
            const type = field.field_type === 'url' ? 'url' : 'text';
            const esc = String(value).replace(/"/g, '&quot;');
            return `<td class="ob-cell" data-site-id="${sid}" data-field-id="${fid}">
                <input type="${type}" value="${esc}" placeholder="—">
            </td>`;
        }

        async function obSaveCell(siteId, fieldId, value) {
            try {
                await fetch('/api/onboarding/data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ site_id: parseInt(siteId), field_id: fieldId, value }),
                });
                if (_obData?.data) {
                    if (!_obData.data[String(siteId)]) _obData.data[String(siteId)] = {};
                    _obData.data[String(siteId)][fieldId] = value;
                }
            } catch(e) { console.warn('obSaveCell failed', e); }
        }

        // ── Manage Fields Modal ───────────────────────────────────────────────────

        function closeManageFields() {
            document.getElementById('obManageFieldsOverlay').classList.remove('visible');
            modalRemove('obManageFieldsOverlay');
            loadOnboarding();
        }

        document.getElementById('obManageFieldsBtn').addEventListener('click', openManageFields);
        document.getElementById('obManageFieldsCloseBtn').addEventListener('click', closeManageFields);
        document.getElementById('obManageFieldsOverlay').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeManageFields();
        });

        async function openManageFields() {
            document.getElementById('obManageFieldsOverlay').classList.add('visible');
            modalPush('obManageFieldsOverlay', closeManageFields);
            await refreshManageFieldsList();
        }

        async function refreshManageFieldsList() {
            const fields = await apiCall('/onboarding/fields').catch(() => []);
            const groups = [];
            const groupMap = {};
            for (const f of fields) {
                if (!groupMap[f.group_name]) { groupMap[f.group_name] = []; groups.push(f.group_name); }
                groupMap[f.group_name].push(f);
            }

            // Populate group datalist
            const dl = document.getElementById('mfGroupList');
            dl.innerHTML = groups.map(g => `<option value="${g}">`).join('');

            let html = '';
            for (const g of groups) {
                const allHidden = groupMap[g].every(f => f.hidden);
                html += `<div class="mf-group">
                    <div class="mf-group-header">
                        <span class="mf-group-name">${g}</span>
                        <button class="btn mf-group-hide-btn" data-group="${g}" data-hidden="${allHidden ? '1' : '0'}">${allHidden ? 'Show Group' : 'Hide Group'}</button>
                    </div>
                    <div>`;
                for (const f of groupMap[g]) {
                    html += `<div class="mf-field-row${f.hidden ? ' mf-field-hidden' : ''}" data-field-id="${f.id}">
                        <span class="mf-field-name">${f.name}</span>
                        <span style="font-size:11px;color:var(--text-muted);">${f.field_type}</span>
                        <button class="mf-btn-icon mf-move-up" title="Move up">↑</button>
                        <button class="mf-btn-icon mf-move-dn" title="Move down">↓</button>
                        <button class="mf-btn-icon mf-hide-btn" title="${f.hidden ? 'Show' : 'Hide'}">${f.hidden ? '👁' : '🚫'}</button>
                        <button class="mf-btn-icon mf-del-btn" title="Delete" style="color:var(--red,#f87171);">✕</button>
                    </div>`;
                }
                html += '</div></div>';
            }
            document.getElementById('mfFieldList').innerHTML = html;

            // Wire events
            document.querySelectorAll('.mf-group-hide-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const g = btn.dataset.group;
                    const hide = btn.dataset.hidden === '0';
                    const gFields = fields.filter(f => f.group_name === g);
                    await Promise.all(gFields.map(f =>
                        fetch(`/api/onboarding/fields/${f.id}`, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ hidden: hide }),
                        })
                    ));
                    await refreshManageFieldsList();
                });
            });

            document.querySelectorAll('.mf-hide-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const row = btn.closest('[data-field-id]');
                    const fid = row.dataset.fieldId;
                    const f = fields.find(x => x.id === fid);
                    await fetch(`/api/onboarding/fields/${fid}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ hidden: !f.hidden }),
                    });
                    await refreshManageFieldsList();
                });
            });

            document.querySelectorAll('.mf-del-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const row = btn.closest('[data-field-id]');
                    const fid = row.dataset.fieldId;
                    const f = fields.find(x => x.id === fid);
                    if (!confirm(`Delete field "${f?.name}"? This removes all saved data for this field.`)) return;
                    await fetch(`/api/onboarding/fields/${fid}`, { method: 'DELETE' });
                    await refreshManageFieldsList();
                });
            });

            // Move up/down: reorder by swapping positions
            document.querySelectorAll('.mf-move-up, .mf-move-dn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const row = btn.closest('[data-field-id]');
                    const fid = row.dataset.fieldId;
                    const idx = fields.findIndex(f => f.id === fid);
                    const isUp = btn.classList.contains('mf-move-up');
                    const swapIdx = isUp ? idx - 1 : idx + 1;
                    if (swapIdx < 0 || swapIdx >= fields.length) return;
                    const posA = fields[idx].position;
                    const posB = fields[swapIdx].position;
                    await Promise.all([
                        fetch(`/api/onboarding/fields/${fields[idx].id}`, {
                            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ position: posB }),
                        }),
                        fetch(`/api/onboarding/fields/${fields[swapIdx].id}`, {
                            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ position: posA }),
                        }),
                    ]);
                    await refreshManageFieldsList();
                });
            });
        }

        document.getElementById('mfAddBtn').addEventListener('click', async () => {
            const name = document.getElementById('mfNewName').value.trim();
            const group = document.getElementById('mfNewGroup').value.trim() || 'General';
            const type = document.getElementById('mfNewType').value;
            if (!name) { document.getElementById('mfNewName').focus(); return; }
            await fetch('/api/onboarding/fields', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, group_name: group, field_type: type }),
            });
            document.getElementById('mfNewName').value = '';
            await refreshManageFieldsList();
        });

        // ── Options Modal (site visibility) ─────────────────────────────────────

        function closeObOptions() {
            document.getElementById('obOptionsOverlay').classList.remove('visible');
            modalRemove('obOptionsOverlay');
            loadOnboarding();
        }

        document.getElementById('obOptionsBtn').addEventListener('click', openObOptions);
        document.getElementById('obOptionsCloseBtn').addEventListener('click', closeObOptions);
        document.getElementById('obOptionsOverlay').addEventListener('click', e => {
            if (e.target === e.currentTarget) closeObOptions();
        });

        async function openObOptions() {
            document.getElementById('obOptionsOverlay').classList.add('visible');
            modalPush('obOptionsOverlay', closeObOptions);
            const sites = await apiCall('/sites').catch(() => []);
            const list = document.getElementById('obOptionsSiteList');
            list.innerHTML = sites.map(s => {
                const hidden = s.hidden_from_onboarding ? 'checked' : '';
                // checkbox checked = visible (hidden_from_onboarding = 0)
                return `<label style="display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);cursor:pointer;">
                    <input type="checkbox" data-site-id="${s.id}" ${hidden ? '' : 'checked'} style="width:16px;height:16px;">
                    <span style="font-size:13px;">${s.name}</span>
                    ${s.url ? `<span style="font-size:11px;color:var(--text-muted);flex:1;text-align:right;overflow:hidden;text-overflow:ellipsis;">${s.url}</span>` : ''}
                </label>`;
            }).join('');

            list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', async () => {
                    const siteId = cb.dataset.siteId;
                    const hidden = cb.checked ? 0 : 1;
                    await fetch(`/api/sites/config/${siteId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ hidden_from_onboarding: hidden }),
                    });
                });
            });
        }

        // ── Site Dashboard — Onboarding section ──────────────────────────────────
        // _esc moved to dom.js

        async function loadSdOnboarding() {
            const el = document.getElementById('sdObContent');
            el.innerHTML = '<div class="sd-section-empty">Loading…</div>';
            if (!_sdSiteId) return;
            try {
                const resp = await apiCall('/onboarding/data');
                const fields = resp.fields || [];
                const siteData = (resp.data || {})[_sdSiteId] || {};

                if (!fields.length) {
                    el.innerHTML = '<div class="sd-section-empty">No onboarding fields — add them in the Onboarding tab.</div>';
                    return;
                }

                // Build ordered groups
                const groups = [];
                const groupMap = {};
                for (const f of fields) {
                    const g = f.group_name || 'General';
                    if (!groupMap[g]) { groupMap[g] = []; groups.push(g); }
                    groupMap[g].push(f);
                }

                let html = '<div class="sd-ob-grid">';
                for (const g of groups) {
                    const gFields = groupMap[g];
                    const allHidden = gFields.every(f => f.hidden);
                    html += `<div class="sd-ob-group${allHidden ? ' sd-ob-hidden' : ''}">`;
                    html += `<div class="sd-ob-group-name">${_esc(g)}</div>`;
                    for (const f of gFields) {
                        const val = siteData[f.id] != null ? siteData[f.id] : (f.default_value || '');
                        const rowHidden = f.hidden ? ' sd-ob-hidden' : '';
                        const lblHidden = f.hidden ? ' sd-ob-hidden-label' : '';
                        html += `<div class="sd-ob-row${rowHidden}" data-field-id="${_esc(f.id)}">`;
                        html += `<span class="sd-ob-label${lblHidden}" title="${_esc(f.name)}">${_esc(f.name)}</span>`;
                        html += `<div class="sd-ob-value">`;
                        if (f.field_type === 'bool') {
                            const on = val === '1' || val === 'true';
                            html += `<span class="sd-ob-bool" data-bool-val="${on ? '1' : '0'}">${on ? '✅' : '⬜'}</span>`;
                        } else if (f.field_type === 'url') {
                            html += `<input class="sd-ob-input" type="url" value="${_esc(val)}" data-field-id="${_esc(f.id)}" placeholder="https://…">`;
                        } else if (f.field_type === 'select') {
                            let opts = [];
                            try { opts = JSON.parse(f.options || '[]'); } catch(e) {}
                            html += `<select class="sd-ob-input sd-ob-select" data-field-id="${_esc(f.id)}">`;
                            html += `<option value="">—</option>`;
                            for (const o of opts) {
                                html += `<option value="${_esc(o)}" ${val === o ? 'selected' : ''}>${_esc(o)}</option>`;
                            }
                            html += `</select>`;
                        } else {
                            html += `<input class="sd-ob-input" type="text" value="${_esc(val)}" data-field-id="${_esc(f.id)}" placeholder="—">`;
                        }
                        html += `</div></div>`;
                    }
                    html += `</div>`;
                }
                html += '</div>';
                el.innerHTML = html;

                // Bool toggles
                el.querySelectorAll('.sd-ob-bool').forEach(span => {
                    span.addEventListener('click', async () => {
                        const fieldId = span.closest('[data-field-id]').dataset.fieldId;
                        const newVal = span.dataset.boolVal === '1' ? '0' : '1';
                        span.dataset.boolVal = newVal;
                        span.textContent = newVal === '1' ? '✅' : '⬜';
                        await _saveObCell(fieldId, newVal);
                    });
                });

                // Text / URL / select inputs
                el.querySelectorAll('.sd-ob-input[data-field-id]').forEach(inp => {
                    inp.addEventListener('change', () => _saveObCell(inp.dataset.fieldId, inp.value));
                    inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
                });

            } catch (e) {
                el.innerHTML = `<div class="sd-section-empty">Failed to load onboarding data.</div>`;
            }
        }

        // sites.js's openSiteDashboard() calls this via the loader it registers
        // here — see registerSdOnboardingLoader in sites.js for why.
        registerSdOnboardingLoader(loadSdOnboarding);

        async function _saveObCell(fieldId, value) {
            await fetch('/api/onboarding/data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ site_id: parseInt(_sdSiteId), field_id: fieldId, value }),
            });
        }

        document.getElementById('sdObShowHiddenBtn').addEventListener('click', () => {
            const content = document.getElementById('sdObContent');
            const btn = document.getElementById('sdObShowHiddenBtn');
            const showing = content.classList.toggle('sd-ob-show-hidden');
            btn.classList.toggle('active', showing);
            btn.textContent = showing ? 'Hide Hidden' : 'Show Hidden';
        });

        document.getElementById('sdObEditCheck').addEventListener('change', e => {
            document.getElementById('sdObContent').classList.toggle('sd-ob-locked', !e.target.checked);
        });

        document.getElementById('obEditCheck').addEventListener('change', e => {
            document.getElementById('obTableContainer').classList.toggle('ob-locked', !e.target.checked);
        });


        registerRoute('onboarding', () => loadOnboarding());
