import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  checkPluginUpdates, fetchRestartStatus, waitForRestart, previewUpgrade, restartThemis, rollbackPlugin, uninstallPlugin, updatePlugin, usePlugins,
  type InstallPreview, type PluginSummary,
} from '../api/plugins';
import { PluginInstallDialog } from '../components/PluginInstallDialog';
import { PageHeader } from '../components/settingsUi';
import { PluginSettingsPage } from '../components/PluginSettingsPage';

const capTone = (status: string) => status === 'serving' ? 'ok' : status === 'error' ? 'err' : status === 'waiting' ? 'warn' : 'idle';
const capTitle = (x: { capability: string; status: string; waiting_on: string[] }) =>
  x.status === 'serving' ? `Serving ${x.capability}`
  : x.status === 'error' ? `Selected for ${x.capability} but failing`
  : x.status === 'waiting' ? `Selected for ${x.capability}, waiting on ${x.waiting_on.join(', ')}`
  : x.status === 'not_selected' ? `Provides ${x.capability} (not the selected provider)`
  : `Provides ${x.capability} (${x.status.replace('_', ' ')})`;

/** Settings → Plugins: every installed plugin. A `page` plugin links to its own sidebar page; a `section` plugin renders
 *  here, collapsible, with the default plugin page as its body. */
export function PluginsPage() {
  const { plugins, pending, loaded, refresh } = usePlugins();
  const [open, setOpen] = useState<string | null>(null);
  const [installing, setInstalling] = useState<InstallPreview | 'new' | null>(null);
  const [notice, setNotice] = useState<Record<string, string>>({});          // per-plugin result of the last action
  const [removing, setRemoving] = useState<string | null>(null);              // uninstall confirmation open for this plugin
  const [removeData, setRemoveData] = useState(false);
  const [restart, setRestart] = useState<{ printing: string[] } | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState('');

  const reconnect = useRef<AbortController | null>(null);
  useEffect(() => () => reconnect.current?.abort(), []);        // leaving the page stops waiting for the restart

  const say = (id: string, msg: string) => setNotice(n => ({ ...n, [id]: msg }));
  const fail = (id: string) => (e: unknown) => say(id, e instanceof Error ? e.message : 'That did not work');

  async function askRestart() {
    setRestartError('');
    try { setRestart({ printing: (await fetchRestartStatus()).printing }); }
    catch (e) { setRestartError(e instanceof Error ? e.message : 'Could not check printers'); }
  }
  async function doRestart(force: boolean) {
    setRestartError('');
    try {
      const r = await restartThemis(force);
      if (r.restarting) {
        setRestarting(true);
        reconnect.current = new AbortController();
        void waitForRestart(reconnect.current.signal).then(back => { if (back) window.location.reload(); });
      } else setRestart({ printing: r.printing ?? [] });
    } catch (e) { setRestartError(e instanceof Error ? e.message : 'Restart failed'); }
  }
  async function checkUpdates(p: PluginSummary) {
    try {
      const r = await checkPluginUpdates(p.id);
      if (!r.update_available) { say(p.id, 'Up to date'); return; }
      setInstalling(await previewUpgrade(p.id));
    } catch (e) { fail(p.id)(e); }
  }

  return (
    <div>
      <PageHeader title="Plugins" sub="Integrations that extend Themis. Each is enabled, configured and (where it has a library) browsed here."
                  actions={<button className="btn primary sm" onClick={() => setInstalling('new')}>Install plugin</button>} />
      {pending.length > 0 && (
        <div className="card" role="status" data-testid="restart-banner" style={{ padding: 12, marginBottom: 12 }}>
          {restarting ? <div>Restarting Themis… this page will reconnect when it is back.</div> : (
            <div className="col gap-2">
              <div className="row between" style={{ alignItems: 'center' }}>
                <div>
                  <strong>Restart Themis to apply {pending.length} pending change{pending.length === 1 ? '' : 's'}</strong>
                  <ul className="small muted" style={{ margin: '4px 0 0 16px', padding: 0 }}>
                    {pending.map(c => <li key={c.plugin_id}>{c.change === 'uninstall' ? 'Uninstall' : c.change === 'install' ? 'Install' : 'Update'} {c.name}{c.change === 'uninstall' ? '' : ` v${c.version}`}</li>)}
                  </ul>
                </div>
                {!restart && <button className="btn primary sm" onClick={() => void askRestart()}>Restart Themis</button>}
              </div>
              {restart && (
                <div className="col gap-2" data-testid="restart-confirm">
                  {restart.printing.length > 0 && (
                    <div role="alert" className="small">Printing right now: <strong>{restart.printing.join(', ')}</strong>. Restarting interrupts the connection to {restart.printing.length === 1 ? 'that printer' : 'those printers'}.</div>
                  )}
                  <div className="row gap-2">
                    <button className="btn sm" onClick={() => setRestart(null)}>Not now</button>
                    <button className="btn primary sm" onClick={() => void doRestart(restart.printing.length > 0)}>
                      {restart.printing.length > 0 ? 'Restart anyway' : 'Restart now'}
                    </button>
                  </div>
                </div>
              )}
              {restartError && <div role="alert" className="small" style={{ color: 'var(--err, #d44)' }}>{restartError}</div>}
            </div>
          )}
        </div>
      )}
      {!loaded && <div className="muted small">Loading…</div>}
      {loaded && plugins.length === 0 && <div className="muted small">No plugins are installed.</div>}
      <div className="col gap-2">
        {plugins.map(p => (
          <div key={p.id} className="card" style={{ padding: 16 }} data-testid={`plugin-${p.id}`}>
            <div className="row between" style={{ alignItems: 'center' }}>
              <div className="col">
                <div style={{ fontWeight: 600 }}>{p.name} <span className="muted small">v{p.version}</span></div>
                {p.description && <div className="muted small">{p.description}</div>}
                {p.provides.length > 0 && (
                  <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 4 }} data-testid={`plugin-caps-${p.id}`}>
                    {p.provides.map(x => (
                      <span key={x.capability} className={`pill ${capTone(x.status)}`} title={capTitle(x)}>
                        <span className="dot" />{x.capability}
                      </span>
                    ))}
                  </div>
                )}
                {p.install && (
                  <div className="tiny muted" data-testid={`plugin-source-${p.id}`}>
                    {p.install.source_url ? `${p.source === 'github' ? 'GitHub' : 'Uploaded'}: ${p.install.source_url}` : p.source}
                    {p.install.commit_sha ? ` @ ${p.install.commit_sha.slice(0, 7)}` : ''}{p.install.publisher ? ` · by ${p.install.publisher}` : ''}
                    {p.install.status === 'pending_restart' && p.install.version !== p.version && ` · v${p.install.version} after restart`}
                  </div>
                )}
                {p.install?.error && <div role="alert" className="small" style={{ color: 'var(--err, #d44)' }}>{p.install.error}</div>}
                {notice[p.id] && <div className="tiny muted" data-testid={`plugin-notice-${p.id}`}>{notice[p.id]}</div>}
              </div>
              <div className="row gap-2" style={{ alignItems: 'center' }}>
                <span className={`pill ${p.error ? 'err' : p.active ? 'ok' : p.enabled ? 'info' : 'idle'}`}>
                  <span className="dot" />{p.install?.status === 'pending_removal' ? 'Uninstalls on restart'
                    : p.loaded === false && p.install?.status === 'pending_restart' ? 'Starts after restart'
                    : p.error ? 'Problem' : p.active ? 'Active' : p.enabled ? 'Enabled' : 'Disabled'}
                </span>
                {p.loaded === false || p.install?.status === 'pending_removal' ? null : !p.enabled
                  ? <button className="btn sm" onClick={() => { updatePlugin(p.id, { enabled: true }).catch((e) => window.alert(e instanceof Error ? e.message : 'Could not enable the plugin')); }}>Enable</button>
                  : p.ui.mode === 'page'
                  ? <Link className="btn sm" to={`/plugins/${p.id}`}>Open</Link>
                  : <button className="btn sm" aria-expanded={open === p.id} onClick={() => setOpen(o => (o === p.id ? null : p.id))}>
                      {open === p.id ? 'Hide' : 'Settings'}
                    </button>}
                {p.install && p.install.status !== 'pending_removal' && (
                  <>
                    {p.install.can_check_updates && <button className="btn sm" onClick={() => void checkUpdates(p)}>Check for updates</button>}
                    {p.install.can_rollback && <button className="btn sm" onClick={() => { rollbackPlugin(p.id).then(() => say(p.id, `Rolls back to v${p.install?.previous_version} on restart`)).catch(fail(p.id)); }}>Roll back</button>}
                    <button className="btn sm" onClick={() => { setRemoving(p.id); setRemoveData(false); }}>Uninstall</button>
                  </>
                )}
              </div>
            </div>
            {removing === p.id && (
              <div className="col gap-2" style={{ marginTop: 12 }} data-testid={`uninstall-confirm-${p.id}`}>
                <div className="small">Uninstall <strong>{p.name}</strong>? It is disabled now and its code is removed at the next restart.</div>
                <label className="row gap-2 small" style={{ alignItems: 'center' }}>
                  <input type="checkbox" checked={removeData} onChange={e => setRemoveData(e.target.checked)} /> Also delete this plugin&apos;s data (tables and settings) — cannot be undone
                </label>
                <div className="row gap-2">
                  <button className="btn sm" onClick={() => setRemoving(null)}>Cancel</button>
                  <button className="btn primary sm" onClick={() => { uninstallPlugin(p.id, removeData).then(() => setRemoving(null)).catch(fail(p.id)); }}>Uninstall</button>
                </div>
              </div>
            )}
            {p.ui.mode === 'section' && open === p.id && <div style={{ marginTop: 12 }}><PluginSettingsPage pluginId={p.id} /></div>}
          </div>
        ))}
      </div>
      {installing && (
        <PluginInstallDialog plugins={plugins} initial={installing === 'new' ? undefined : installing}
                             onClose={() => setInstalling(null)} onInstalled={() => { setInstalling(null); refresh(); }} />
      )}
    </div>
  );
}
