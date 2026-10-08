import { Navigate, NavLink, useParams } from 'react-router-dom';
import { featuresOf, updatePlugin, usePlugins } from '../api/plugins';
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
        <div className="small muted" style={{ marginBottom: 10 }}>{plugin.name} is disabled, so it has no pages and no navigation entry.</div>
        <button className="btn sm" onClick={() => { updatePlugin(plugin.id, { enabled: true }).catch((e) => window.alert(e instanceof Error ? e.message : 'Could not enable the plugin')); }}>Enable {plugin.name}</button>
      </div>
    );
  }

  const declared = plugin.ui.tabs.length > 0 ? plugin.ui.tabs : [{ id: 'default', label: 'Settings', renderer: 'default' as const }];
  // A component tab that needs a capability the plugin lacks does not exist (e.g. mappings without preset links).
  const tabs = declared.filter(t => {
    const needs = t.renderer === 'component' ? COMPONENT_TABS[`${plugin.id}/${t.id}`]?.requires : undefined;
    return !needs || featuresOf(plugin).includes(needs);
  });
  if (tabs.length === 0) return <div className="card" style={{ padding: 24 }}><div className="small muted">{plugin.name} has no pages available.</div></div>;
  const active = tabs.find(t => t.id === tab);
  if (!active) return <Navigate to={`/plugins/${plugin.id}/${tabs[0].id}`} replace />;

  const Component = active.renderer === 'component' ? COMPONENT_TABS[`${plugin.id}/${active.id}`]?.Component : undefined;
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
