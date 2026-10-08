import { useState } from 'react';
import { useCapabilityList, type CapabilityInfo } from '../api/capabilities';
import { setCapabilityProvider } from '../api/plugins';
import { FieldRow, PageHeader } from '../components/settingsUi';

function chip(c: CapabilityInfo): { text: string; tone: string } {
  switch (c.status) {
    case 'serving': return { text: 'Serving', tone: 'ok' };
    case 'waiting': return { text: `Waiting on ${c.waiting_on.join(', ')}`, tone: 'info' };
    case 'error': return { text: c.error || 'Provider error', tone: 'err' };
    case 'disabled': return { text: 'Provider disabled', tone: 'idle' };
    case 'no_provider': return { text: 'Provider missing', tone: 'idle' };
    case 'dormant': return { text: 'Dormant', tone: 'idle' };
    default: return { text: 'None selected', tone: 'idle' };
  }
}

/** Settings → Capabilities: every capability Themis knows (core + plugin-defined) and which plugin serves it. */
export function CapabilitiesPage() {
  const { items, loaded, error, reload } = useCapabilityList();
  const [msg, setMsg] = useState<string | null>(null);

  async function choose(cap: CapabilityInfo, pluginId: string) {
    setMsg(null);
    try { await setCapabilityProvider(cap.id, pluginId || null); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    reload();
  }

  return (
    <div>
      <PageHeader title="Capabilities" sub="Which plugin serves each capability. Only one provider is used per capability." />
      {(error || msg) && <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: 8 }}>{error || msg}</div>}
      {loaded && !error && items.length === 0 && <div className="muted small">No capabilities are known.</div>}
      {items.map(c => {
        const { text, tone } = chip(c);
        const hint = [c.description, c.definer ? `Defined by ${c.definer}` : '',
          c.requires_by.length ? `Needed by: ${c.requires_by.map(r => r.plugin_id).join(', ')}` : ''].filter(Boolean).join(' · ');
        return (
          <FieldRow key={c.id} label={c.label} hint={hint || c.id}>
            <div className="col gap-2">
              <select className="select" aria-label={`${c.label} provider`} value={c.selected ?? ''}
                      disabled={c.providers.length === 0} onChange={e => choose(c, e.target.value)}>
                <option value="">None</option>
                {c.providers.map(p => <option key={p.plugin_id} value={p.plugin_id}>{p.name}{p.enabled ? '' : ' (disabled)'}</option>)}
              </select>
              {c.providers.length === 0 && c.status !== 'dormant' && <span className="small muted">No plugin provides this</span>}
              <span className={`pill ${tone}`}><span className="dot" />{text}</span>
            </div>
          </FieldRow>
        );
      })}
    </div>
  );
}
