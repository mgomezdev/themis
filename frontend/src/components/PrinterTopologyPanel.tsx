import { useState } from 'react';
import { refreshPrinterTopology, type PrinterTopology } from '../api/printers';

export function PrinterTopologyPanel({ printerId, initial, onRefreshed }: {
  printerId: number; initial?: PrinterTopology | null; onRefreshed?: () => void;
}) {
  const [topology, setTopology] = useState(initial);
  const [sizes, setSizes] = useState('');
  const current = topology ?? initial;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = async () => {
    setError(null);
    const nozzleSizes = sizes.trim() ? sizes.split(',').map(n => Number(n.trim())) : null;
    if (nozzleSizes?.some(n => !Number.isFinite(n) || n <= 0)) {
      setError('Enter positive nozzle sizes separated by commas.'); return;
    }
    setBusy(true);
    try {
      const out = await refreshPrinterTopology(printerId, nozzleSizes ? { nozzle_sizes_mm: nozzleSizes } : undefined);
      setTopology(out.topology); onRefreshed?.();
    } catch (e) { setError(e instanceof Error ? e.message : 'Refresh failed'); }
    finally { setBusy(false); }
  };
  return <section className="col gap-2" aria-label="Printer topology">
    <div className="tag-key">Tools and filament positions</div>
    {current && <>
      <div className="tiny">{current.positions.length} configured positions · {current.positions.filter(p => p.filament !== null).length} loaded</div>
      {current.tools.map(t => <div className="tiny" key={t.id}>{t.id}: {t.nozzle_mm} mm</div>)}
    </>}
    <label className="label">Installed nozzle sizes (mm)
      <input className="input" value={sizes} onChange={e => setSizes(e.target.value)} placeholder="Reported by printer, or enter e.g. 0.4, 0.6" />
    </label>
    <div className="tiny muted">One size per tool, in the order shown. Leave blank to use reported or previously configured sizes.</div>
    <button className="btn sm" type="button" onClick={() => void refresh()} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh topology'}</button>
    {error && <div role="alert" className="tiny">{error}</div>}
  </section>;
}
