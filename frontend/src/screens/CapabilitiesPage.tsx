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

const REASON: Record<NonNullable<CapabilityInfo['dormant_default']>['reason'], string> = {
  plugin_removed: 'its plugin is no longer installed',
  plugin_disabled: 'its plugin is disabled',
  provider_unavailable: 'it failed to start',
};

const MODE_NOTE: Record<CapabilityInfo['mode'], string> = {
  exclusive: '',
  routed: 'Every enabled provider is used, each for the resources bound to it.',
  fan_out: 'Every enabled provider receives every event; one failing never blocks the others.',
  choose_one: 'The default is used unless a resource has its own preference; an unavailable provider blocks the operation, it is never swapped for another.',
};

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
        const everyProvider = c.mode === 'routed' || c.mode === 'fan_out';       // nothing to select: all enabled providers serve it
        // Offer only providers that can serve; the current default stays listed (flagged) so a dormant choice is visible, not erased.
        const offered = c.mode === 'choose_one'
          ? c.providers.filter(p => p.status === 'serving' || p.plugin_id === c.selected)
          : c.providers;
        return (
          <FieldRow key={c.id} label={c.label} hint={[hint || c.id, MODE_NOTE[c.mode]].filter(Boolean).join(' · ')}>
            <div className="col gap-2">
              {everyProvider ? (
                <ul className="col gap-1" aria-label={`${c.label} providers`} style={{ margin: 0, paddingLeft: 16 }}>
                  {c.providers.map(p => (
                    <li key={p.plugin_id} className="small">{p.name}{' '}
                      <span className="muted">{p.status === 'serving' ? '— serving' : p.enabled ? `— ${p.status}` : '— disabled'}</span></li>
                  ))}
                </ul>
              ) : (
                <select className="select" aria-label={`${c.label} ${c.mode === 'choose_one' ? 'default ' : ''}provider`} value={c.selected ?? ''}
                        disabled={c.providers.length === 0} onChange={e => choose(c, e.target.value)}>
                  <option value="">None</option>
                  {offered.map(p => (
                    <option key={p.plugin_id} value={p.plugin_id}>
                      {p.name}{p.plugin_id === c.dormant_default?.plugin_id ? ' (unavailable)' : p.enabled ? '' : ' (disabled)'}
                    </option>
                  ))}
                </select>
              )}
              {c.providers.length === 0 && c.status !== 'dormant' && <span className="small muted">No plugin provides this</span>}
              {c.dormant_default && (
                <div role="alert" className="small" style={{ color: 'var(--warn)' }}>
                  The default provider “{c.dormant_default.plugin_id}” is unavailable: {REASON[c.dormant_default.reason]}. Work that needs it is
                  blocked until you choose another — Themis never picks one for you.
                </div>
              )}
              {!everyProvider && <span className={`pill ${tone}`}><span className="dot" />{text}</span>}
            </div>
          </FieldRow>
        );
      })}
    </div>
  );
}
