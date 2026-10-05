import { useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlugins } from '../api/plugins';
import { PageHeader } from '../components/settingsUi';
import { PluginSettingsPage } from '../components/PluginSettingsPage';

/** Settings → Plugins: every installed plugin. A `page` plugin links to its own sidebar page; a `section` plugin renders
 *  here, collapsible, with the default plugin page as its body. */
export function PluginsPage() {
  const { plugins, loaded } = usePlugins();
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div>
      <PageHeader title="Plugins" sub="Integrations that extend Themis. Each is enabled, configured and (where it has a library) browsed here." />
      {!loaded && <div className="muted small">Loading…</div>}
      {loaded && plugins.length === 0 && <div className="muted small">No plugins are installed.</div>}
      <div className="col gap-2">
        {plugins.map(p => (
          <div key={p.id} className="card" style={{ padding: 16 }} data-testid={`plugin-${p.id}`}>
            <div className="row between" style={{ alignItems: 'center' }}>
              <div className="col">
                <div style={{ fontWeight: 600 }}>{p.name} <span className="muted small">v{p.version}</span></div>
                <div className="muted small">{p.description || p.kind.replace(/_/g, ' ')}</div>
              </div>
              <div className="row gap-2" style={{ alignItems: 'center' }}>
                <span className={`pill ${p.error ? 'err' : p.active ? 'ok' : p.enabled ? 'info' : 'idle'}`}>
                  <span className="dot" />{p.error ? 'Problem' : p.active ? 'Active' : p.enabled ? 'Enabled' : 'Disabled'}
                </span>
                {p.ui.mode === 'page'
                  ? <Link className="btn sm" to={`/plugins/${p.id}`}>Open</Link>
                  : <button className="btn sm" aria-expanded={open === p.id} onClick={() => setOpen(o => (o === p.id ? null : p.id))}>
                      {open === p.id ? 'Hide' : 'Settings'}
                    </button>}
              </div>
            </div>
            {p.ui.mode === 'section' && open === p.id && <div style={{ marginTop: 12 }}><PluginSettingsPage pluginId={p.id} /></div>}
          </div>
        ))}
      </div>
    </div>
  );
}
