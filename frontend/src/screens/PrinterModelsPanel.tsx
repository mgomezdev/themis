import { useEffect, useState } from 'react';
import { fetchPrinterModels, setPrinterModelEnabled, type PrinterModelEntry } from '../api/printers';
import { ModelSlicerProfiles } from '../components/ModelSlicerProfiles';

const REASON: Record<NonNullable<PrinterModelEntry['dormant_reason']>, string> = {
  plugin_removed: 'plugin removed',
  plugin_disabled: 'plugin disabled',
  model_removed: 'no longer offered by its plugin',
};

/** The registry's models grouped by manufacturer, each with an on/off switch for the "add printer" pickers and its slicer machine
 *  profile (set once per model, BIZ-294; the slicing provider suggests one when it can). A dormant model
 *  (its plugin is gone or disabled) stays listed while printers reference it, flagged with why. */
export function PrinterModelsPanel() {
  const [models, setModels] = useState<PrinterModelEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);   // the model whose slicer profile editor is expanded

  useEffect(() => {
    fetchPrinterModels().then(r => setModels(Array.isArray(r) ? r : [])).catch(e => setError(e instanceof Error ? e.message : 'Failed to load models'));
  }, []);

  async function toggle(m: PrinterModelEntry) {
    setError(null);
    try {
      const updated = await setPrinterModelEnabled(m.id, !m.enabled);
      setModels(prev => (prev ?? []).map(x => (x.id === updated.id ? { ...x, ...updated } : x)));
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
              <div key={m.id} className="col gap-2" style={{ marginBottom: 6 }}>
                <div className="row gap-2" style={{ alignItems: 'center' }}>
                  <label className="row gap-2" style={{ alignItems: 'center' }}>
                    <input type="checkbox" checked={m.enabled} onChange={() => toggle(m)} aria-label={`${m.manufacturer_name} ${m.display_name}`} />
                    <span className="small">{m.display_name}</span>
                  </label>
                  {m.dormant_reason && <span className="tiny muted">dormant — {REASON[m.dormant_reason]}</span>}
                  {m.printer_count > 0 && <span className="tiny muted">{m.printer_count} printer{m.printer_count === 1 ? '' : 's'}</span>}
                  <span className="tiny" style={{ color: m.slicer_profile_count ? 'var(--text-3)' : 'var(--warn)' }}>
                    {m.slicer_profile_count ? `${m.slicer_profile_count} slicer profile${m.slicer_profile_count === 1 ? '' : 's'}` : 'no slicer profile'}
                  </span>
                  <button className="btn ghost sm" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>
                    Slicer profile
                  </button>
                </div>
                {open === m.id && (
                  <div className="card" style={{ padding: 12, marginLeft: 24 }}>
                    <ModelSlicerProfiles modelUuid={m.id} modelLabel={`${m.manufacturer_name} ${m.display_name}`}
                                         onChange={rows => setModels(prev => (prev ?? []).map(x => (x.id === m.id && x.slicer_profile_count !== rows.length
                                           ? { ...x, slicer_profile_count: rows.length } : x)))} />
                  </div>
                )}
              </div>
            ))}
          </fieldset>
        ))}
      </div>
    </div>
  );
}
