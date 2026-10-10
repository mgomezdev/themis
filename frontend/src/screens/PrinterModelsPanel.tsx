import { useEffect, useState } from 'react';
import { fetchPrinterModels, setPrinterModelEnabled, type PrinterModelEntry } from '../api/printers';

const REASON: Record<NonNullable<PrinterModelEntry['dormant_reason']>, string> = {
  plugin_removed: 'plugin removed',
  plugin_disabled: 'plugin disabled',
  model_removed: 'no longer offered by its plugin',
};

/** The registry's models grouped by manufacturer, each with an on/off switch for the "add printer" pickers. A dormant model
 *  (its plugin is gone or disabled) stays listed while printers reference it, flagged with why. */
export function PrinterModelsPanel() {
  const [models, setModels] = useState<PrinterModelEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPrinterModels().then(r => setModels(Array.isArray(r) ? r : [])).catch(e => setError(e instanceof Error ? e.message : 'Failed to load models'));
  }, []);

  async function toggle(m: PrinterModelEntry) {
    setError(null);
    try {
      const updated = await setPrinterModelEnabled(m.id, !m.enabled);
      setModels(prev => (prev ?? []).map(x => (x.id === updated.id ? updated : x)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not update the model');
    }
  }

  const groups = new Map<string, PrinterModelEntry[]>();
  for (const m of models ?? []) groups.set(m.manufacturer_name, [...(groups.get(m.manufacturer_name) ?? []), m]);

  return (
    <div className="card" style={{ padding: 16 }}>
      {error && <div role="alert" style={{ color: 'var(--err)' }}>{error}</div>}
      {models === null && !error && <span className="muted small">Loading…</span>}
      <div className="col gap-3">
        {[...groups].map(([maker, list]) => (
          <fieldset key={maker} style={{ border: 'none', padding: 0, margin: 0 }}>
            <legend className="small" style={{ fontWeight: 500 }}>{maker}</legend>
            {list.map(m => (
              <label key={m.id} className="row gap-2" style={{ alignItems: 'center' }}>
                <input type="checkbox" checked={m.enabled} onChange={() => toggle(m)} aria-label={`${m.manufacturer_name} ${m.display_name}`} />
                <span className="small">{m.display_name}</span>
                {m.dormant_reason && <span className="tiny muted">dormant — {REASON[m.dormant_reason]}</span>}
                {m.printer_count > 0 && <span className="tiny muted">{m.printer_count} printer{m.printer_count === 1 ? '' : 's'}</span>}
              </label>
            ))}
          </fieldset>
        ))}
      </div>
    </div>
  );
}
