import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchPlugin, setCapabilityProvider, testPlugin, updatePlugin, usePlugins,
  type JsonSchemaProperty, type PluginDetail,
} from '../api/plugins';
import { FieldRow, PageHeader, Toggle } from './settingsUi';
import { INVENTORY_CAPABILITY } from '../api/inventory';
import { InventoryProviderPanel } from './InventoryProviderPanel';

/** The default plugin page every plugin can reference: header (name, version, docs, enable, health), a connection form
 *  generated from the plugin's settings model (secrets are write-only), a Test connection button and, for an active
 *  inventory provider, its sync / pending-writes / suspended-tracking panel. */

type Draft = Record<string, unknown>;

function propType(p: JsonSchemaProperty): 'boolean' | 'number' | 'text' {
  const types = [p.type, ...(p.anyOf ?? []).map(a => a.type)].flat().filter((t): t is string => !!t && t !== 'null');
  if (types.includes('boolean')) return 'boolean';
  if (types.includes('integer') || types.includes('number')) return 'number';
  return 'text';
}

const humanize = (key: string) => key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());

export function PluginSettingsPage({ pluginId }: { pluginId: string }) {
  const { refresh } = usePlugins();
  const [plugin, setPlugin] = useState<PluginDetail | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [secretDraft, setSecretDraft] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const adopt = useCallback((d: PluginDetail) => { setPlugin(d); setDraft(d.settings); setSecretDraft({}); }, []);

  useEffect(() => {
    let alive = true;
    setPlugin(null); setLoadError('');
    fetchPlugin(pluginId).then(d => { if (alive) adopt(d); })
      .catch(e => { if (alive) setLoadError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [pluginId, adopt]);

  const fields = useMemo(() => {
    if (!plugin) return [];
    const props = plugin.settings_schema?.properties ?? {};
    return Object.entries(props).filter(([k]) => !(plugin.secret_fields ?? []).includes(k));
  }, [plugin]);

  if (loadError) return <div role="alert" style={{ color: 'var(--err)' }}>{loadError}</div>;
  if (!plugin) return <div className="muted small">Loading…</div>;
  const pluginId_ = plugin.id;

  const required = new Set(plugin.settings_schema?.required ?? []);
  const unselected = plugin.provides.filter(p => !p.selected);            // capabilities this plugin could serve but does not
  const isSelected = plugin.provides.some(p => p.selected);
  const health: { tone: string; label: string } = plugin.error ? { tone: 'err', label: 'Problem' }
    : !plugin.enabled ? { tone: 'idle', label: 'Disabled' }
    : isSelected ? { tone: 'ok', label: 'Active' } : { tone: 'info', label: 'Enabled, not selected' };

  const patchBody = () => {
    const settings: Draft = {};
    for (const [k, p] of fields) {
      const v = draft[k];
      settings[k] = propType(p) === 'number' ? (v === '' || v == null ? null : Number(v)) : (v === '' ? null : v);
    }
    return { settings, secrets: secretDraft };
  };

  async function run(label: string, fn: () => Promise<PluginDetail | void>) {
    setBusy(true); setMsg(null);
    try {
      const d = await fn();
      if (d) adopt(d);
      setMsg({ ok: true, text: label });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }

  const save = () => run('Saved', () => updatePlugin(pluginId_, patchBody()));
  const setEnabled = (enabled: boolean) => run(enabled ? 'Enabled' : 'Disabled', () => updatePlugin(pluginId_, { enabled }));
  const makeActive = (capability: string) => run('Selected as the active provider', async () => {
    await setCapabilityProvider(capability, pluginId_);
    refresh();
    return fetchPlugin(pluginId_);                    // the page reads its selection state from the detail: re-adopt it
  });

  async function test() {
    setBusy(true); setTestMsg(null);
    try {
      const r = await testPlugin(pluginId_, patchBody());
      setTestMsg({ ok: r.ok, text: r.ok ? `Connected${r.version ? ` (version ${r.version})` : ''}` : r.message || 'Could not connect' });
    } catch (e) {
      setTestMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }

  return (
    <div data-testid="plugin-page">
      <PageHeader
        title={plugin.name}
        sub={`${plugin.description || plugin.provides.map(p => p.capability).join(', ')} · v${plugin.version}`}
        actions={<span className={`pill ${health.tone}`} data-testid="plugin-health"><span className="dot" />{health.label}</span>}
      />
      {plugin.docs_url && <div className="small" style={{ marginBottom: 12 }}><a href={plugin.docs_url} target="_blank" rel="noreferrer">Documentation</a></div>}
      {plugin.error && <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: 12 }}>{plugin.error}</div>}

      <FieldRow label={`Enable ${plugin.name}`} hint="When off, Themis ignores this plugin entirely.">
        <Toggle checked={plugin.enabled} onChange={setEnabled} />
      </FieldRow>
      {plugin.enabled && unselected.map(p => (
        <FieldRow key={p.capability} label={`Use for ${p.capability}`}
                  hint={`Another provider is selected for ${p.capability} (or none). Only the selected one is used.`}>
          <button className="btn sm" onClick={() => makeActive(p.capability)} disabled={busy}
                  aria-label={unselected.length > 1 ? `Use ${plugin.name} for ${p.capability}` : `Use ${plugin.name}`}>Use {plugin.name}</button>
        </FieldRow>
      ))}
      {plugin.provides.filter(p => p.status === 'waiting').map(p => (
        <div key={p.capability} role="status" className="small muted" style={{ marginBottom: 8 }}>
          {p.capability}: waiting on {p.waiting_on.join(', ')}
        </div>
      ))}

      {fields.map(([key, prop]) => {
        const type = propType(prop);
        const label = prop.title ?? humanize(key);
        return (
          <FieldRow key={key} label={`${label}${required.has(key) ? ' *' : ''}`} hint={prop.description}>
            {type === 'boolean'
              ? <Toggle checked={!!draft[key]} onChange={v => setDraft(d => ({ ...d, [key]: v }))} />
              : <input className="input" aria-label={label} type={type === 'number' ? 'number' : 'text'}
                       min={type === 'number' ? prop.minimum : undefined}
                       value={draft[key] == null ? '' : String(draft[key])}
                       placeholder={prop.default != null ? String(prop.default) : ''}
                       onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))} />}
          </FieldRow>
        );
      })}

      {(plugin.secret_fields ?? []).map(key => {
        const prop = plugin.settings_schema?.properties?.[key] ?? {};
        const label = prop.title ?? humanize(key);
        const stored = plugin.secrets?.[key];
        const touched = key in secretDraft;
        return (
          <FieldRow key={key} label={label} hint={prop.description}>
            <div className="row gap-2">
              <input className="input" type="password" autoComplete="new-password" aria-label={label}
                     value={secretDraft[key] ?? ''}
                     placeholder={stored ? 'Set ✓ — type to replace' : 'Not set'}
                     onChange={e => setSecretDraft(d => ({ ...d, [key]: e.target.value }))} />
              {stored && !touched && <button className="btn ghost sm" aria-label={`Clear ${label}`} onClick={() => setSecretDraft(d => ({ ...d, [key]: '' }))}>Clear</button>}
            </div>
            {touched && secretDraft[key] === '' && <div className="tiny muted" style={{ marginTop: 4 }}>Will be cleared on save</div>}
          </FieldRow>
        );
      })}

      <div className="row gap-2" style={{ padding: '16px 0', alignItems: 'center' }}>
        <button className="btn primary sm" onClick={save} disabled={busy}>Save</button>
        <button className="btn sm" onClick={test} disabled={busy}>Test connection</button>
        {msg && <span role={msg.ok ? 'status' : 'alert'} className="small" style={{ color: msg.ok ? 'var(--ok)' : 'var(--err)' }}>{msg.text}</span>}
        {testMsg && <span data-testid="test-result" className="small" style={{ color: testMsg.ok ? 'var(--ok)' : 'var(--err)' }}>{testMsg.text}</span>}
      </div>

      {plugin.provides.some(p => p.capability === INVENTORY_CAPABILITY && p.status === 'serving') && <InventoryProviderPanel plugin={plugin} />}
    </div>
  );
}
