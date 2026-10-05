import { Navigate, NavLink, useParams } from 'react-router-dom';
import { usePlugins } from '../api/plugins';
import { PluginSettingsPage } from '../components/PluginSettingsPage';
import { SchemaTab } from '../components/SchemaTab';
import { COMPONENT_TABS } from '../plugins/registry';

/** `/plugins/:id/:tab?` — a plugin's own page: one tab strip, each tab rendered by its declared renderer. */
export function PluginPage() {
  const { id = '', tab } = useParams();
  const { plugins, loaded } = usePlugins();
  const plugin = plugins.find(p => p.id === id);

  if (!loaded) return <div className="muted small">Loading…</div>;
  if (!plugin) return <div className="card" style={{ padding: 24 }}><div className="small muted">No plugin “{id}” is installed.</div></div>;
  if (!plugin.enabled) {
    return (
      <div className="card" style={{ padding: 24 }} data-testid="plugin-disabled">
        <div className="small muted">{plugin.name} is disabled. Enable it from Settings → Plugins to use its pages.</div>
      </div>
    );
  }

  const tabs = plugin.ui.tabs.length > 0 ? plugin.ui.tabs : [{ id: 'default', label: 'Settings', renderer: 'default' as const }];
  const active = tabs.find(t => t.id === tab);
  if (!active) return <Navigate to={`/plugins/${plugin.id}/${tabs[0].id}`} replace />;

  const Component = active.renderer === 'component' ? COMPONENT_TABS[`${plugin.id}/${active.id}`] : undefined;
  return (
    <div>
      {tabs.length > 1 && (
        <nav className="settings-tabs" style={{ display: 'flex', gap: 4, marginBottom: 16 }} aria-label={`${plugin.name} tabs`}>
          {tabs.map(t => (
            <NavLink key={t.id} to={`/plugins/${plugin.id}/${t.id}`} className={({ isActive }) => `settings-tab ${isActive ? 'active' : ''}`}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      )}
      {active.renderer === 'default' && <PluginSettingsPage pluginId={plugin.id} />}
      {active.renderer === 'schema' && <SchemaTab pluginId={plugin.id} tabId={active.id} />}
      {active.renderer === 'component' && (Component
        ? <Component />
        : <div role="alert" className="small" style={{ color: 'var(--err)' }}>This tab needs a component that is not part of this build.</div>)}
    </div>
  );
}
