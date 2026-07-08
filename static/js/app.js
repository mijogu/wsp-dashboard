        // ============================================================================
        // Imports — core modules extracted during the ES-module split
        // ============================================================================
        import { appState, CONFIG } from './state.js';
        import {
            apiCall, getStatus, unlock, getSettings, saveSettings,
            getUptimeRobot, getCloudflare, getCloudflareAnalytics, getCloudflareZoneSettings,
            getMainWP, getMainWPUpdates, exportData, importData,
        } from './api.js';
        import { modalPush, modalRemove, escapeHtml, _esc, safeJsonParse, showStatus, downloadCsv } from './dom.js';
        import { dbg, dbgError } from './debug.js';
        import {
            navigate, applyRouteFromHash, switchTab, registerRoute, checkWiring,
            buildHash, _getLastSubview, _saveLastSubview,
        } from './router.js';
        import { initLogPanel, toggleLogPanel, fetchLogs, renderLogs } from './logpanel.js';
        import { renderOverview } from './overview.js';
        import { renderUptime } from './uptime.js';
        import { renderCloudflare } from './cloudflare.js';
        import { renderMainWP } from './mainwp.js';
        import { loadDbStats, loadCachedHistory } from './history.js';
        import { checkRegressionAvailability, loadRegressionSiteList } from './regression.js';
        import './sites.js';
        import './linkcheck.js';
        import './onboarding.js';
        import './checklists.js';
        // Each of the above (plus cloudflare.js, history.js, and regression.js
        // above) registers its own route on import — see router.js's registry.
        // sites.js/linkcheck.js/onboarding.js/checklists.js have no bindings
        // app.js still needs directly, so these are side-effect-only imports;
        // omitting one of these would silently break its tab (the wiring
        // audit in debug.js would flag the missing route handler at boot).

        dbg('module', 'app.js loaded');

        // ============================================================================
        // Initialize App
        // ============================================================================
        async function initApp() {
            try {
                const status = await getStatus();
                appState.hasConfig = status.hasConfig;
                if (status.commit) {
                    document.getElementById('buildHash').textContent = status.commit;
                }

                if (status.unlocked) {
                    // Server auto-unlocked from saved session
                    appState.unlocked = true;
                    await loadAllData();
                    startAutoRefresh();
                    loadDbStats();
                    loadCachedHistory(true);
                    checkRegressionAvailability();
                    loadRegressionSiteList();
                    if (!location.hash || location.hash === '#' || location.hash === '#/') {
                        history.replaceState(null, '', '#/overview');
                    }
                    applyRouteFromHash();
                    return;
                }

                appState.unlocked = false;

                if (status.hasConfig) {
                    // Returning user — unlock existing config
                    document.getElementById('passphraseTitle').textContent = 'Dashboard Locked';
                    document.getElementById('passphraseDesc').textContent = 'Enter your passphrase to unlock and view monitoring data.';
                    document.getElementById('unlockBtn').textContent = 'Unlock';
                    // Pre-check "Remember me" if a session existed before (user opted in previously)
                    if (status.hasSession) {
                        document.getElementById('rememberMe').checked = true;
                    }
                } else {
                    // First run — set a new passphrase
                    document.getElementById('passphraseTitle').textContent = 'Welcome';
                    document.getElementById('passphraseDesc').textContent = 'Choose a passphrase to encrypt your API keys. You\'ll enter this each time you start the dashboard.';
                    document.getElementById('unlockBtn').textContent = 'Set Passphrase';
                }
                showPassphraseModal();
            } catch (error) {
                console.error('Failed to initialize app:', error);
                alert('Failed to connect to the dashboard server. Is server.py running?');
            }
        }

        // ============================================================================
        // Passphrase Modal
        // ============================================================================
        function showPassphraseModal() {
            document.getElementById('passphraseOverlay').classList.add('visible');
        }

        function hidePassphraseModal() {
            document.getElementById('passphraseOverlay').classList.remove('visible');
        }

        document.getElementById('unlockBtn').addEventListener('click', async () => {
            const passphrase = document.getElementById('passphraseInput').value.trim();
            if (!passphrase) return;
            const remember = document.getElementById('rememberMe').checked;

            try {
                document.getElementById('passphraseError').style.display = 'none';
                const result = await unlock(passphrase, remember);
                if (result.error) {
                    document.getElementById('passphraseError').textContent = result.error;
                    document.getElementById('passphraseError').style.display = 'block';
                } else {
                    appState.unlocked = true;
                    hidePassphraseModal();
                    await loadAllData();
                    startAutoRefresh();
                    loadDbStats();
                    loadCachedHistory(true);
                    checkRegressionAvailability();
                    loadRegressionSiteList();
                    if (!location.hash || location.hash === '#' || location.hash === '#/') {
                        history.replaceState(null, '', '#/overview');
                    }
                    applyRouteFromHash();
                }
            } catch (error) {
                document.getElementById('passphraseError').textContent = 'Error unlocking dashboard';
                document.getElementById('passphraseError').style.display = 'block';
            }
        });

        document.getElementById('passphraseInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                document.getElementById('unlockBtn').click();
            }
        });

        // ============================================================================
        // Settings Page
        // ============================================================================
        async function loadSettingsData() {
            try {
                const settings = await getSettings();
                document.getElementById('urApiKey').value = settings.urApiKey || '';
                document.getElementById('cfApiToken').value = settings.cfApiToken || '';
                document.getElementById('cfAccountId').value = settings.cfAccountId || '';
                document.getElementById('mwpUrl').value = settings.mwpUrl || '';
                document.getElementById('mwpApiKey').value = settings.mwpApiKey || '';
                document.getElementById('settingsStatus').style.display = 'none';
                // Restore refresh interval from localStorage
                const saved = localStorage.getItem('refreshInterval');
                if (saved) document.getElementById('refreshInterval').value = saved;
            } catch (error) {
                showStatus('settingsStatus', 'Failed to load settings', 'error');
            }
        }

        document.getElementById('settingsBtn').addEventListener('click', () => navigate({ tab: 'settings' }));
        document.getElementById('sitesBtn').addEventListener('click', () => navigate({ tab: 'sites' }));
        document.getElementById('onboardingBtn').addEventListener('click', () => navigate({ tab: 'onboarding' }));
        document.getElementById('checklistsBtn').addEventListener('click', () => navigate({ tab: 'checklists' }));

        document.getElementById('saveSettingsBtn').addEventListener('click', async () => {
            const btn = document.getElementById('saveSettingsBtn');
            btn.disabled = true;
            btn.textContent = 'Saving…';
            const settings = {
                urApiKey: document.getElementById('urApiKey').value.trim(),
                cfApiToken: document.getElementById('cfApiToken').value.trim(),
                cfAccountId: document.getElementById('cfAccountId').value.trim(),
                mwpUrl: document.getElementById('mwpUrl').value.trim(),
                mwpApiKey: document.getElementById('mwpApiKey').value.trim(),
            };

            try {
                const result = await saveSettings(settings);
                if (result.ok) {
                    showStatus('settingsStatus', 'Settings saved successfully', 'success');
                    setTimeout(() => loadAllData(), 1000);
                } else {
                    showStatus('settingsStatus', 'Failed to save settings', 'error');
                }
            } catch (error) {
                showStatus('settingsStatus', 'Error saving settings', 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Save Settings';
            }
        });

        document.getElementById('exportBtn').addEventListener('click', async () => {
            try {
                const result = await exportData();
                const element = document.createElement('a');
                element.setAttribute('href', `data:text/json;charset=utf-8,${encodeURIComponent(JSON.stringify(JSON.parse(atob(result.data)), null, 2))}`);
                element.setAttribute('download', `dashboard-backup-${new Date().toISOString().split('T')[0]}.json`);
                element.style.display = 'none';
                document.body.appendChild(element);
                element.click();
                document.body.removeChild(element);
                showStatus('settingsStatus', 'Settings exported', 'success');
            } catch (error) {
                showStatus('settingsStatus', 'Export failed', 'error');
            }
        });

        document.getElementById('importBtn').addEventListener('click', () => {
            document.getElementById('importFile').click();
        });

        document.getElementById('importFile').addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            try {
                const text = await file.text();
                const json = JSON.parse(text);
                const base64 = btoa(JSON.stringify(json));
                const result = await importData(base64);
                if (result.ok) {
                    showStatus('settingsStatus', 'Settings imported successfully', 'success');
                    setTimeout(() => loadAllData(), 1000);
                }
            } catch (error) {
                showStatus('settingsStatus', 'Import failed', 'error');
            }
        });

        // ============================================================================
        // Data Loading
        // ============================================================================
        async function loadAllData() {
            // Fetch each source independently — unconfigured services return
            // null instead of blowing up the whole dashboard
            const [ur, cf, mwpSites, mwpUpdates] = await Promise.all([
                getUptimeRobot().catch(e => { console.warn('Uptime Robot:', e.message); return null; }),
                getCloudflare().catch(e => { console.warn('Cloudflare:', e.message); return null; }),
                getMainWP().catch(e => { console.warn('MainWP sites:', e.message); return null; }),
                getMainWPUpdates().catch(e => { console.warn('MainWP updates:', e.message); return null; }),
            ]);

            appState.data.uptimeRobot = ur;
            appState.data.cloudflare = cf;
            appState.data.mainwp = mwpSites;
            appState.data.mainwpUpdates = mwpUpdates;

            appState.lastRefreshTime = new Date();
            updateLastUpdatedTime();

            renderOverview();
            renderUptime();
            await renderCloudflare();
            await renderMainWP();
        }

        function updateLastUpdatedTime() {
            if (!appState.lastRefreshTime) return;
            const now = new Date();
            const diff = Math.floor((now - appState.lastRefreshTime) / 1000);
            let timeStr;

            if (diff < 60) {
                timeStr = 'just now';
            } else if (diff < 3600) {
                timeStr = `${Math.floor(diff / 60)}m ago`;
            } else {
                timeStr = appState.lastRefreshTime.toLocaleTimeString();
            }

            document.getElementById('lastUpdated').textContent = timeStr;
        }

        // ============================================================================
        // Auto Refresh
        // ============================================================================
        function startAutoRefresh() {
            const savedInterval = localStorage.getItem(CONFIG.REFRESH_INTERVAL_KEY);
            const interval = parseInt(savedInterval) || CONFIG.DEFAULT_REFRESH_INTERVAL;

            document.getElementById('refreshInterval').value = interval;

            clearInterval(appState.autoRefreshTimer);
            appState.autoRefreshTimer = setInterval(() => {
                loadAllData();
                updateLastUpdatedTime();
            }, interval);
        }

        document.getElementById('refreshInterval').addEventListener('change', (e) => {
            const interval = parseInt(e.target.value);
            localStorage.setItem(CONFIG.REFRESH_INTERVAL_KEY, interval);
            startAutoRefresh();
        });

        document.getElementById('refreshBtn').addEventListener('click', () => {
            loadAllData();
            updateLastUpdatedTime();
        });

        // Update time display every minute
        setInterval(() => {
            updateLastUpdatedTime();
        }, 60000);

        // ============================================================================
        // Sub-view route handlers
        // ============================================================================
        // Hash routing itself (parseHash/buildHash/navigate/_applyRoute/switchTab)
        // lives in router.js. Every per-tab sub-view applier has been peeled into
        // its own feature module, which registers its own route on import — see
        // the imports block above. Settings is the one exception: it's small
        // enough (~90 lines) to stay part of the app shell rather than get its
        // own module.
        registerRoute('settings', () => loadSettingsData());

        // Render: Overview Tab, Render: Uptime Tab — moved to overview.js, uptime.js
        // Render: Cloudflare Tab — moved to cloudflare.js (registers its own route)
        // Render: MainWP Tab — moved to mainwp.js

        // Log Panel — moved to logpanel.js (imported above: initLogPanel,
        // toggleLogPanel, fetchLogs, renderLogs).
        // Regression Tab — moved to regression.js (registers its own route)

        // ============================================================================
        // Startup
        // ============================================================================
        // Surface anything that slips past a route handler's own try/catch —
        // in particular, a module that fails to import shows up here with the
        // failing source URL, which a plain "page is blank" report wouldn't.
        window.addEventListener('error', (e) => {
            dbgError('window', e.message || 'uncaught error', e.error);
        });
        window.addEventListener('unhandledrejection', (e) => {
            dbgError('window', 'unhandled promise rejection', e.reason);
        });

        window.addEventListener('DOMContentLoaded', () => {
            initLogPanel();
            // All registerRoute(...) calls above have run by this point (module
            // top-level code executes before DOMContentLoaded fires), so the
            // registry is complete and safe to audit.
            checkWiring();
            initApp();
            fetchLogs();
        });

        // Sites Tab (list, detail, dashboard) — moved to sites.js (registers its own route)
        // Link Checker — moved to linkcheck.js (registers its own route)
        // Onboarding (field grid + Site Dashboard panel) — moved to onboarding.js
        // Onboarding Checklists — moved to checklists.js


