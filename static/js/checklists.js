// ============================================================================
// Onboarding Checklists — client onboarding step-by-step tracker
// ============================================================================
// Extracted from the original monolithic index.html <script> during the
// ES-module split.

import { modalPush, modalRemove, _esc } from './dom.js';
import { apiCall } from './api.js';
import { registerRoute } from './router.js';

import { dbg } from './debug.js';
dbg('module', 'checklists.js loaded');

        // ── Onboarding Checklists event wiring ───────────────────────────────────

        // Back button
        document.getElementById('obcBackBtn').addEventListener('click', () => {
            _obcCurrentId = null;
            document.getElementById('obcDetail').style.display = 'none';
            document.getElementById('obcList').style.display = '';
            loadChecklists();
        });

        // Edit toggle
        document.getElementById('obcEditCheck').addEventListener('change', e => {
            document.getElementById('obcDetailContainer').classList.toggle('obc-locked', !e.target.checked);
        });

        // Filter chips
        document.querySelectorAll('.obc-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                document.querySelectorAll('.obc-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                _obcFilter = chip.dataset.status;
                loadChecklists();
            });
        });

        // New Onboarding button → show inline modal
        document.getElementById('obcNewBtn').addEventListener('click', () => {
            _showObcNewModal();
        });

        function _showObcNewModal() {
            // Create a lightweight inline modal
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay visible';
            overlay.style.zIndex = '310';
            overlay.innerHTML = `
                <div class="modal" style="max-width:440px;width:95vw;">
                    <h2>New Onboarding Checklist</h2>
                    <div class="form-group">
                        <label>Client Name *</label>
                        <input type="text" class="field-input" id="obcNewName" placeholder="Acme Corp" autofocus>
                    </div>
                    <div class="form-group">
                        <label>Site URL</label>
                        <input type="url" class="field-input" id="obcNewUrl" placeholder="https://example.com">
                    </div>
                    <div class="form-group">
                        <label>Client Type</label>
                        <div style="display:flex;gap:16px;margin-top:4px;">
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcClientType" value="new" checked> New Client
                            </label>
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcClientType" value="current"> Current Client
                            </label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Project Type</label>
                        <div style="display:flex;gap:16px;margin-top:4px;">
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcProjectType" value="new_site" checked> New Site/Domain
                            </label>
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcProjectType" value="redesign"> Redesign
                            </label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Host</label>
                        <div style="display:flex;gap:16px;align-items:center;margin-top:4px;flex-wrap:wrap;">
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcHost" value="siteground" checked> Siteground
                            </label>
                            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;">
                                <input type="radio" name="obcHost" value="other"> Other:
                            </label>
                            <input type="text" id="obcNewCustomHost" class="field-input" style="width:140px;display:none;" placeholder="host name">
                        </div>
                    </div>
                    <div class="modal-buttons" style="margin-top:20px;">
                        <button class="btn" id="obcNewCancelBtn">Cancel</button>
                        <button class="btn btn-primary" id="obcNewCreateBtn">Create</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);

            // Show/hide custom host input
            overlay.querySelectorAll('input[name="obcHost"]').forEach(r => {
                r.addEventListener('change', () => {
                    document.getElementById('obcNewCustomHost').style.display =
                        overlay.querySelector('input[name="obcHost"]:checked').value === 'other' ? '' : 'none';
                });
            });

            const close = () => { overlay.remove(); modalRemove('obcNewModal'); };
            modalPush('obcNewModal', close);

            overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
            document.getElementById('obcNewCancelBtn').addEventListener('click', close);
            document.getElementById('obcNewCreateBtn').addEventListener('click', async () => {
                const name = document.getElementById('obcNewName').value.trim();
                if (!name) { document.getElementById('obcNewName').focus(); return; }
                const hostRadio = overlay.querySelector('input[name="obcHost"]:checked').value;
                const customHost = hostRadio === 'other' ? (document.getElementById('obcNewCustomHost').value.trim()) : 'siteground';
                const projectType = overlay.querySelector('input[name="obcProjectType"]:checked').value;
                const clientType = overlay.querySelector('input[name="obcClientType"]:checked').value;
                close();
                await _obcCreateNew({
                    client_name: name,
                    site_url: document.getElementById('obcNewUrl').value.trim(),
                    is_new_client: clientType === 'new',
                    is_redesign: projectType === 'redesign',
                    is_new_site: projectType === 'new_site',
                    custom_host: customHost,
                });
            });

            // Focus
            setTimeout(() => document.getElementById('obcNewName').focus(), 50);
        }

        // ── Onboarding Checklists ─────────────────────────────────────────────────

        let _obcTemplate = null;  // { sections, steps }
        let _obcFilter = 'active';
        let _obcCurrentId = null;
        let _obcCurrentData = {};  // { step_id: { value, completed_at } }

        async function loadChecklists() {
            const container = document.getElementById('obcListContainer');
            container.innerHTML = '<div style="padding:16px;color:var(--text-muted);font-size:13px;">Loading…</div>';

            // Ensure we have the template (sections/steps from CSV)
            if (!_obcTemplate) {
                try { _obcTemplate = await apiCall('/checklists/template'); } catch(e) { /* ignore */ }
            }

            try {
                const qs = _obcFilter ? `?status=${_obcFilter}` : '';
                const rows = await apiCall(`/checklists${qs}`);
                renderObcList(rows, container);
            } catch(e) {
                container.innerHTML = `<div class="empty-state"><h3>Error</h3><p>${_esc(e.message)}</p></div>`;
            }
        }

        function renderObcList(rows, container) {
            if (!rows.length) {
                const label = _obcFilter || 'onboarding checklists';
                container.innerHTML = `<div class="empty-state"><h3>No ${_esc(label)} checklists</h3><p>Click "+ New Onboarding" to create one.</p></div>`;
                return;
            }
            let html = `<table class="obc-list-table"><thead><tr>
                <th>Client</th><th>Site URL</th><th>Progress</th><th>Status</th><th>Linked Site</th><th>Updated</th><th></th>
            </tr></thead><tbody>`;
            rows.forEach(row => {
                const pct = row.progress_pct ?? 0;
                const badge = `<span class="obc-badge obc-badge-${row.status}">${_esc(row.status)}</span>`;
                const linked = row.linked_site_name ? _esc(row.linked_site_name) : '<span style="color:var(--text-muted);font-size:12px;">— not linked —</span>';
                const updated = (row.updated_at || '').slice(0, 16).replace('T', ' ');
                html += `<tr class="obc-list-row" data-obc-id="${row.id}">
                    <td style="font-weight:500;">${_esc(row.client_name)}</td>
                    <td style="font-size:12px;color:var(--text-muted);">${_esc(row.site_url || '')}</td>
                    <td>
                        <div class="obc-progress-wrap">
                            <div class="obc-progress-bar"><div class="obc-progress-fill" style="width:${pct}%"></div></div>
                            <span class="obc-progress-label">${row.progress_completed}/${row.progress_total}</span>
                        </div>
                    </td>
                    <td>${badge}</td>
                    <td>${linked}</td>
                    <td style="font-size:12px;color:var(--text-muted);">${updated}</td>
                    <td>
                        <button class="btn" style="font-size:11px;padding:3px 10px;" data-obc-open="${row.id}">Open</button>
                        <button class="btn" style="font-size:11px;padding:3px 10px;" data-obc-delete="${row.id}">Delete</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table>';
            container.innerHTML = html;

            // Row click → open; button overrides
            container.querySelectorAll('tr.obc-list-row').forEach(tr => {
                tr.addEventListener('click', e => {
                    if (e.target.closest('button')) return;
                    openObcDetail(parseInt(tr.dataset.obcId));
                });
            });
            container.querySelectorAll('[data-obc-open]').forEach(btn => {
                btn.addEventListener('click', e => { e.stopPropagation(); openObcDetail(parseInt(btn.dataset.obcOpen)); });
            });
            container.querySelectorAll('[data-obc-delete]').forEach(btn => {
                btn.addEventListener('click', e => {
                    e.stopPropagation();
                    const id = parseInt(btn.dataset.obcDelete);
                    if (!confirm('Delete this checklist and all its progress?')) return;
                    apiCall(`/checklists/${id}`, 'DELETE').then(() => loadChecklists());
                });
            });
        }

        async function openObcDetail(checklistId) {
            _obcCurrentId = checklistId;
            document.getElementById('obcList').style.display = 'none';
            document.getElementById('obcDetail').style.display = '';

            // Reset edit toggle
            document.getElementById('obcEditCheck').checked = false;
            document.getElementById('obcDetailContainer').classList.add('obc-locked');

            document.getElementById('obcDetailContainer').innerHTML =
                '<div style="padding:16px;color:var(--text-muted);font-size:13px;">Loading…</div>';

            try {
                const [cl, availSites] = await Promise.all([
                    apiCall(`/checklists/${checklistId}`),
                    apiCall('/checklists/available-sites'),
                ]);
                _obcCurrentData = cl.data || {};
                renderObcDetail(cl, availSites);
            } catch(e) {
                document.getElementById('obcDetailContainer').innerHTML =
                    `<div class="empty-state"><h3>Error</h3><p>${_esc(e.message)}</p></div>`;
            }
        }

        function renderObcDetail(cl, availSites) {
            const tmpl = _obcTemplate || { sections: [], steps: [] };
            const allSteps = tmpl.steps || [];

            // Condition flags
            const isNewClient = !!cl.is_new_client;
            const isRedesign = !!cl.is_redesign;
            const isNewSite = !!cl.is_new_site;
            const customHost = (cl.custom_host || '').trim().toLowerCase();
            const isCustomHost = !!(customHost && customHost !== 'siteground');

            // Build condition badges
            const condBadges = [
                isNewClient ? 'New Client' : 'Current Client',
                isRedesign ? 'Redesign' : 'New Site',
                isCustomHost ? `Host: ${_esc(cl.custom_host)}` : 'Siteground',
            ].map(t => `<span class="obc-cond-badge">${t}</span>`).join('');

            // Link/unlink control
            let linkHtml;
            if (cl.mainwp_site_id) {
                const linkedName = availSites.__linked_name || `site #${cl.mainwp_site_id}`;
                linkHtml = `
                    <div class="obc-link-row">
                        <span class="obc-link-label">Linked to MainWP:</span>
                        <strong style="font-size:13px;">${_esc(String(cl.mainwp_site_id))}</strong>
                        <button class="btn" id="obcUnlinkBtn" style="font-size:11px;">Unlink</button>
                    </div>`;
            } else {
                const options = availSites.map(s =>
                    `<option value="${s.id}">${_esc(s.name)} — ${_esc(s.url || '')}</option>`
                ).join('');
                linkHtml = `
                    <div class="obc-link-row">
                        <span class="obc-link-label">Link to MainWP site:</span>
                        <select class="field-input obc-link-select" id="obcLinkSelect">
                            <option value="">— select site —</option>
                            ${options}
                        </select>
                        <button class="btn btn-primary" id="obcLinkBtn" style="font-size:11px;">Link</button>
                    </div>`;
            }

            // Progress calculation
            const applicableSteps = allSteps.filter(s => _obcStepApplies(s, cl));
            const completedCount = applicableSteps.filter(s => {
                const v = (_obcCurrentData[s.id] || {}).value;
                return v && v !== '0' && v !== 'false';
            }).length;
            const totalCount = applicableSteps.length;
            const pct = totalCount ? Math.round(completedCount / totalCount * 100) : 0;

            // Header
            let html = `
            <div class="obc-detail-header">
                <div class="obc-detail-title">${_esc(cl.client_name)}</div>
                ${cl.site_url ? `<div class="obc-detail-url">${_esc(cl.site_url)}</div>` : ''}
                <div class="obc-cond-badges">${condBadges}</div>
                ${linkHtml}
                <div class="obc-detail-progress">
                    <div class="obc-detail-progress-bar">
                        <div class="obc-detail-progress-fill" id="obcProgressFill" style="width:${pct}%"></div>
                    </div>
                    <div class="obc-detail-progress-label" id="obcProgressLabel">${completedCount} of ${totalCount} applicable steps complete</div>
                </div>
            </div>`;

            // Steps by section
            const sections = tmpl.sections || [];
            sections.forEach(section => {
                const sectionSteps = allSteps.filter(s => s.section === section);
                if (!sectionSteps.length) return;
                html += `<div class="obc-section">
                    <div class="obc-section-title">${_esc(section)}</div>`;
                sectionSteps.forEach(step => {
                    html += _obcRenderStep(step, cl);
                });
                html += '</div>';
            });
            // Steps with no section
            const noSection = allSteps.filter(s => !s.section);
            if (noSection.length) {
                html += `<div class="obc-section">`;
                noSection.forEach(step => { html += _obcRenderStep(step, cl); });
                html += '</div>';
            }

            const container = document.getElementById('obcDetailContainer');
            container.innerHTML = html;

            // Wire up link/unlink buttons
            const linkBtn = document.getElementById('obcLinkBtn');
            if (linkBtn) {
                linkBtn.addEventListener('click', async () => {
                    const sel = document.getElementById('obcLinkSelect');
                    if (!sel.value) return;
                    await apiCall(`/checklists/${_obcCurrentId}/link`, 'POST', { mainwp_site_id: parseInt(sel.value) });
                    openObcDetail(_obcCurrentId);
                });
            }
            const unlinkBtn = document.getElementById('obcUnlinkBtn');
            if (unlinkBtn) {
                unlinkBtn.addEventListener('click', async () => {
                    await apiCall(`/checklists/${_obcCurrentId}/unlink`, 'POST', {});
                    openObcDetail(_obcCurrentId);
                });
            }

            // Wire up step inputs
            container.querySelectorAll('[data-step-id]').forEach(el => {
                const stepId = el.dataset.stepId;
                if (el.type === 'checkbox') {
                    el.addEventListener('change', () => _obcSaveCell(stepId, el.checked ? '1' : ''));
                } else if (el.tagName === 'SELECT') {
                    el.addEventListener('change', () => _obcSaveCell(stepId, el.value));
                } else {
                    el.addEventListener('blur', () => _obcSaveCell(stepId, el.value));
                }
            });

            // Wire up description toggles
            container.querySelectorAll('[data-desc-toggle]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const desc = document.getElementById(`obcDesc-${btn.dataset.descToggle}`);
                    if (desc) desc.classList.toggle('visible');
                });
            });
        }

        function _obcStepApplies(step, cl) {
            const isNewClient = !!cl.is_new_client;
            const isRedesign = !!cl.is_redesign;
            const isNewSite = !!cl.is_new_site;
            const customHost = (cl.custom_host || '').trim().toLowerCase();
            const isCustomHost = !!(customHost && customHost !== 'siteground');
            if (step.requires_new_client && !isNewClient) return false;
            if (step.requires_current_client && isNewClient) return false;
            if (step.requires_redesign && !isRedesign) return false;
            if (step.requires_new_site && !isNewSite) return false;
            if (step.requires_custom_host && !isCustomHost) return false;
            return true;
        }

        function _obcRenderStep(step, cl) {
            const applies = _obcStepApplies(step, cl);
            const disabledClass = applies ? '' : ' obc-step-disabled';
            const saved = (_obcCurrentData[step.id] || {}).value || '';

            let inner = '';
            if (step.field_type === 'bool') {
                const checked = (saved === '1' || saved === 'true') ? 'checked' : '';
                inner = `<div class="obc-step-check">
                    <input type="checkbox" data-step-id="${_esc(step.id)}" ${checked}>
                    <span class="obc-step-name">${_esc(step.name)}</span>
                </div>`;
            } else if (step.field_type === 'select') {
                const options = (step.options || []).map(o =>
                    `<option value="${_esc(o)}" ${saved === o ? 'selected' : ''}>${_esc(o)}</option>`
                ).join('');
                inner = `<div class="obc-step-select">
                    <span class="obc-step-select-label">${_esc(step.name)}</span>
                    <select class="field-input obc-step-dropdown" data-step-id="${_esc(step.id)}">
                        <option value="">—</option>${options}
                    </select>
                </div>`;
            } else {
                // text or url
                inner = `<div class="obc-step-text">
                    <span class="obc-step-text-label">${_esc(step.name)}</span>
                    <input type="${step.field_type === 'url' ? 'url' : 'text'}"
                        class="obc-step-input"
                        data-step-id="${_esc(step.id)}"
                        value="${_esc(saved)}"
                        placeholder="…">
                </div>`;
            }

            const descBtn = step.description
                ? `<button class="obc-step-desc-btn" data-desc-toggle="${_esc(step.id)}" title="Show instructions">?</button>`
                : '';
            const descEl = step.description
                ? `<div class="obc-step-desc" id="obcDesc-${_esc(step.id)}">${_esc(step.description)}</div>`
                : '';

            return `<div class="obc-step${disabledClass}">
                <div style="flex:1;min-width:0;">${inner}${descEl}</div>
                ${descBtn}
            </div>`;
        }

        async function _obcSaveCell(stepId, value) {
            if (!_obcCurrentId) return;
            _obcCurrentData[stepId] = { value, completed_at: value ? new Date().toISOString() : null };
            try {
                await apiCall(`/checklists/${_obcCurrentId}/cell`, 'POST', { step_id: stepId, value });
                _obcUpdateProgress();
            } catch(e) { /* silent */ }
        }

        function _obcUpdateProgress() {
            const tmpl = _obcTemplate || { steps: [] };
            // We need the current checklist conditions — read from DOM badges
            // Simpler: re-fetch from stored _obcCurrentData + template
            const fill = document.getElementById('obcProgressFill');
            const label = document.getElementById('obcProgressLabel');
            if (!fill || !label) return;
            const applicable = tmpl.steps.filter(s => {
                // We don't have cl here; trust the DOM's disabled state instead
                const el = document.querySelector(`[data-step-id="${s.id}"]`);
                if (!el) return false;
                return !el.closest('.obc-step-disabled');
            });
            const completed = applicable.filter(s => {
                const v = (_obcCurrentData[s.id] || {}).value;
                return v && v !== '0' && v !== 'false';
            }).length;
            const total = applicable.length;
            const pct = total ? Math.round(completed / total * 100) : 0;
            fill.style.width = pct + '%';
            label.textContent = `${completed} of ${total} applicable steps complete`;
        }

        async function _obcCreateNew(form) {
            const body = {
                client_name: form.client_name.trim(),
                site_url: form.site_url.trim(),
                is_new_client: form.is_new_client,
                is_redesign: form.is_redesign,
                is_new_site: form.is_new_site,
                custom_host: form.custom_host.trim(),
            };
            if (!body.client_name) return;
            const result = await apiCall('/checklists', 'POST', body);
            if (result.id) openObcDetail(result.id);
        }

        registerRoute('checklists', () => loadChecklists());
