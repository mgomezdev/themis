import { useEffect, useState } from 'react';
import { getFileEligibility, setFileEligibility } from '../api/files';
import { fetchPrinterModels, type PrinterModelEntry } from '../api/printers';

/** Which printer models a pre-sliced G-code file may be sent to (BIZ-263). Unknown (legacy) eligibility is shown as such —
 *  never as a match — and every job for it asks for explicit confirmation until a set is saved here. */
export function FileEligibilityEditor({ fileId, onSaved }: { fileId: number; onSaved?: () => void }) {
  const [models, setModels] = useState<PrinterModelEntry[]>([]);
  const [known, setKnown] = useState<boolean | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sources, setSources] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([fetchPrinterModels(), getFileEligibility(fileId)]).then(([all, el]) => {
      if (!live) return;
      setModels(Array.isArray(all) ? all : []);
      setKnown(el.known);
      setSelected(new Set(el.models.map(m => m.model_uuid)));
      setSources(Object.fromEntries(el.models.map(m => [m.model_uuid, m.source])));
    }).catch(e => live && setError(e instanceof Error ? e.message : 'Could not load eligibility'));
    return () => { live = false; };
  }, [fileId]);

  // Offer the enabled models, plus any already on the file (a model the user later disabled must stay visible and removable).
  const offered = models.filter(m => (m.enabled && !m.dormant) || selected.has(m.id));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const el = await setFileEligibility(fileId, [...selected]);
      setKnown(el.known);
      setSources(Object.fromEntries(el.models.map(m => [m.model_uuid, m.source])));
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  }

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="col gap-2" data-testid="file-eligibility" style={{ marginTop: 12 }}>
      <div className="small" style={{ fontWeight: 500 }}>Machine eligibility</div>
      {known === false && (
        <div className="tiny" role="status" style={{ color: 'var(--warn)' }}>
          Unknown — this file has no recorded machine. Every job for it needs your confirmation. Tick the printer models it is for.
        </div>
      )}
      {error && <div className="tiny" role="alert" style={{ color: 'var(--err)' }}>{error}</div>}
      {offered.map(m => (
        <label key={m.id} className="row gap-2" style={{ alignItems: 'center' }}>
          <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggle(m.id)}
                 aria-label={`${m.manufacturer_name} ${m.display_name}`} />
          <span className="small">{m.manufacturer_name} {m.display_name}</span>
          {sources[m.id] === 'equivalent' && <span className="tiny muted">equivalent</span>}
        </label>
      ))}
      <button className="btn sm" disabled={busy || known === null} onClick={save}>Save eligibility</button>
    </div>
  );
}
