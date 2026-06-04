import { useState, useEffect } from 'react';
import type { MachinePreset } from '../api/printers';

const uniq = (xs: string[]) => [...new Set(xs)].sort();

/**
 * Cascading make → model → nozzle picker over the OrcaSlicer machine catalog.
 * Resolves a selection to a machine-preset name and reports it via onChange.
 */
export function MachinePicker({
  catalog, value, onChange,
}: {
  catalog: MachinePreset[];
  value: string;
  onChange: (presetName: string) => void;
}) {
  const [vendor, setVendor] = useState('');
  const [model, setModel] = useState('');
  const [nozzle, setNozzle] = useState('');

  // Initialise the three selects from the current preset once the catalog is present.
  useEffect(() => {
    if (!catalog.length || vendor) return;
    const e = catalog.find(c => c.name === value);
    if (e) { setVendor(e.vendor); setModel(e.printer_model); setNozzle(e.nozzle); }
  }, [catalog, value, vendor]);

  const vendors = uniq(catalog.map(c => c.vendor));
  const models = uniq(catalog.filter(c => c.vendor === vendor).map(c => c.printer_model));
  const nozzles = uniq(catalog.filter(c => c.vendor === vendor && c.printer_model === model).map(c => c.nozzle));

  const pickVendor = (v: string) => { setVendor(v); setModel(''); setNozzle(''); onChange(''); };
  const pickModel = (m: string) => { setModel(m); setNozzle(''); onChange(''); };
  const pickNozzle = (nz: string) => {
    setNozzle(nz);
    const matches = catalog.filter(c => c.vendor === vendor && c.printer_model === model && c.nozzle === nz);
    const chosen = matches.find(c => c.source === 'system') ?? matches[0];
    onChange(chosen ? chosen.name : '');
  };

  if (catalog.length === 0) {
    return (
      <div className="tiny muted">
        No OrcaSlicer machine profiles found — add profiles in OrcaSlicer, then use Settings → "Rescan profiles".
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 110px', gap: 8 }}>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-make">Make</label>
        <select id="mp-make" aria-label="Make" className="select" value={vendor}
                onChange={e => pickVendor(e.target.value)}>
          <option value="">— make —</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-model">Model</label>
        <select id="mp-model" aria-label="Model" className="select" value={model} disabled={!vendor}
                onChange={e => pickModel(e.target.value)}>
          <option value="">— model —</option>
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-nozzle">Nozzle</label>
        <select id="mp-nozzle" aria-label="Nozzle" className="select" value={nozzle} disabled={!model}
                onChange={e => pickNozzle(e.target.value)}>
          <option value="">—</option>
          {nozzles.map(n => <option key={n} value={n}>{n} mm</option>)}
        </select>
      </div>
    </div>
  );
}
