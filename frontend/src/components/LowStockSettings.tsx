import { useEffect, useState } from 'react';
import {
  filamentDisplayName, getLowStock, saveLowStock, type ApiFilament, type LowStockConfig,
} from '../api/spoolman';

const toNum = (s: string): number | null => (s.trim() === '' || Number.isNaN(Number(s)) ? null : Number(s));

/** Low-inventory alerts: a default threshold plus per-filament overrides. Alerts fire as the `spool.low` event. */
export function LowStockSettings({ filaments }: { filaments: ApiFilament[] }) {
  const [loaded, setLoaded] = useState(false);
  const [defaultG, setDefaultG] = useState('');
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [pickFilament, setPickFilament] = useState('');
  const [pickGrams, setPickGrams] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getLowStock()
      .then(c => { if (!alive) return; setDefaultG(c.default_g != null ? String(c.default_g) : ''); setOverrides(c.overrides ?? {}); setLoaded(true); })
      .catch(e => { if (alive) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); setLoaded(true); } });
    return () => { alive = false; };
  }, []);

  const nameOf = (id: string) => {
    const f = filaments.find(x => String(x.id) === id);
    return f ? filamentDisplayName(f) : `Filament #${id}`;
  };

  function addOverride() {
    const grams = toNum(pickGrams);
    if (!pickFilament || grams == null || grams < 0) return;
    setOverrides(o => ({ ...o, [pickFilament]: grams }));
    setPickFilament(''); setPickGrams('');
  }

  async function save() {
    setSaving(true); setMsg(null);
    const body: LowStockConfig = { default_g: toNum(defaultG), overrides };
    try {
      const cfg = await saveLowStock(body);
      setDefaultG(cfg.default_g != null ? String(cfg.default_g) : ''); setOverrides(cfg.overrides);
      setMsg({ ok: true, text: 'Saved' });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setSaving(false); }
  }

  const available = filaments.filter(f => !(String(f.id) in overrides));

  return (
    <div data-testid="low-stock" style={{ padding: '20px 0', borderBottom: '1px solid var(--border-1)' }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Low-inventory alerts</div>
      <div className="muted small" style={{ marginBottom: 14, maxWidth: 560, lineHeight: 1.5 }}>
        When a spool drops below its threshold Themis raises a <code>spool.low</code> event once (webhook and
        notification channels that include it); it re-arms when the spool is refilled or replaced. Checked at each sync.
      </div>

      {!loaded ? <div className="muted small">Loading…</div> : (
        <div className="col gap-3">
          <label className="row gap-2" style={{ alignItems: 'center' }}>
            <span style={{ width: 150, fontSize: 13 }}>Default threshold</span>
            <input className="input" type="number" min="0" step="1" placeholder="Off" aria-label="Default threshold (g)"
                   value={defaultG} onChange={e => setDefaultG(e.target.value)} style={{ width: 110 }} />
            <span className="muted small">grams remaining</span>
          </label>

          <div className="col gap-2">
            <div className="small muted">Per-filament thresholds (override the default)</div>
            {Object.keys(overrides).length === 0 && <div className="muted small">None.</div>}
            {Object.entries(overrides).map(([id, g]) => (
              <div key={id} className="row gap-2" data-testid={`override-${id}`} style={{ alignItems: 'center' }}>
                <span style={{ flex: 1, fontSize: 13 }}>{nameOf(id)}</span>
                <span className="num">{g} g</span>
                <button className="btn ghost sm" aria-label={`Remove threshold for ${nameOf(id)}`}
                        onClick={() => setOverrides(o => { const { [id]: _drop, ...rest } = o; return rest; })}>Remove</button>
              </div>
            ))}
            <div className="row gap-2" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
              <select className="select" aria-label="Filament" value={pickFilament} onChange={e => setPickFilament(e.target.value)}
                      style={{ minWidth: 200 }}>
                <option value="">Choose a filament…</option>
                {available.map(f => <option key={f.id} value={f.id}>{filamentDisplayName(f)}</option>)}
              </select>
              <input className="input" type="number" min="0" step="1" placeholder="grams" aria-label="Threshold for filament (g)"
                     value={pickGrams} onChange={e => setPickGrams(e.target.value)} style={{ width: 100 }} />
              <button className="btn sm" onClick={addOverride}
                      disabled={!pickFilament || toNum(pickGrams) == null || (toNum(pickGrams) ?? 0) < 0}>Add</button>
            </div>
          </div>

          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <button className="btn sm primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save thresholds'}</button>
            {msg && <span role={msg.ok ? 'status' : 'alert'} className="small" style={{ color: msg.ok ? 'var(--ok)' : 'var(--err)' }}>{msg.text}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
