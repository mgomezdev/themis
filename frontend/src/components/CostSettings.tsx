import { useEffect, useState } from 'react';
import { getCostConfig, saveCostConfig } from '../api/costs';

const toNum = (s: string): number | null => (s.trim() === '' || Number.isNaN(Number(s)) ? null : Number(s));

/** Shop machine/labour hourly rates. They price every project's expenses live, past jobs included. */
export function CostSettings() {
  const [machine, setMachine] = useState('');
  const [labour, setLabour] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getCostConfig()
      .then(c => { if (!alive) return; setMachine(String(c.machine_rate_per_hour)); setLabour(String(c.labour_rate_per_hour)); setLoaded(true); })
      .catch(e => { if (alive) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); setLoaded(true); } });
    return () => { alive = false; };
  }, []);

  const m = toNum(machine);
  const l = toNum(labour);
  const valid = m != null && m >= 0 && l != null && l >= 0;

  async function save() {
    if (!valid) return;
    setSaving(true); setMsg(null);
    try {
      const c = await saveCostConfig({ machine_rate_per_hour: m!, labour_rate_per_hour: l! });
      setMachine(String(c.machine_rate_per_hour)); setLabour(String(c.labour_rate_per_hour));
      setMsg({ ok: true, text: 'Saved' });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ padding: 28 }} data-testid="cost-settings">
      <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Costs</h2>
      <div className="muted small" style={{ margin: '6px 0 18px', maxWidth: 560, lineHeight: 1.5 }}>
        What it really costs to make things. Machine time (completed prints × the rate of the printer they ran on) and
        logged labour are added to filament and bought-in parts in each project's expenses and in customer profit.
        Rates apply <strong>live</strong>: changing one re-prices past jobs too — nothing is locked in when a job prints.
      </div>
      {!loaded ? <div className="muted small">Loading…</div> : (
        <div className="col gap-3">
          <label className="row gap-2" style={{ alignItems: 'center' }}>
            <span style={{ width: 170, fontSize: 13 }}>Machine rate</span>
            <input className="input" type="number" min="0" step="0.01" aria-label="Machine rate per hour" style={{ width: 110 }}
                   value={machine} onChange={e => setMachine(e.target.value)} />
            <span className="muted small">$ per hour of print time (power, wear, depreciation). A printer can override it in its settings.</span>
          </label>
          <label className="row gap-2" style={{ alignItems: 'center' }}>
            <span style={{ width: 170, fontSize: 13 }}>Labour rate</span>
            <input className="input" type="number" min="0" step="0.01" aria-label="Labour rate per hour" style={{ width: 110 }}
                   value={labour} onChange={e => setLabour(e.target.value)} />
            <span className="muted small">$ per hour of logged labour (setup, post-processing, assembly, packing).</span>
          </label>
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <button className="btn primary sm" disabled={!valid || saving} onClick={save}>{saving ? 'Saving…' : 'Save rates'}</button>
            {msg && <span role={msg.ok ? 'status' : 'alert'} className="small" style={{ color: msg.ok ? 'var(--ok)' : 'var(--err)' }}>{msg.text}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
