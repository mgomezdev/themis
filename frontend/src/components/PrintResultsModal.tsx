import { useState } from 'react';
import { markPlateCleared, type PlateResults } from '../api/printers';

/**
 * Print results (BIZ-364), asked when a plate holding a project job is cleared: per part, how many failed (the rest succeeded).
 * Failed parts go back into their print group's queue on their own; one that keeps failing on different printers is held on
 * its project. Submitting clears the plate.
 */
export function PrintResultsModal({ printerId, pending, onClose, onDone }: {
  printerId: string;
  pending: PlateResults;
  onClose: () => void;
  onDone: () => void;
}) {
  const [failed, setFailed] = useState<Record<number, number>>(
    Object.fromEntries(pending.parts.map(p => [p.project_item_id, 0])));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fillAll = (all: boolean) => setFailed(Object.fromEntries(pending.parts.map(p => [p.project_item_id, all ? p.quantity : 0])));

  async function submit() {
    if (pending.job_id == null) return;
    setSaving(true);
    setError(null);
    try {
      await markPlateCleared(printerId, {
        job_id: pending.job_id,
        results: pending.parts.filter(p => failed[p.project_item_id] > 0)
          .map(p => ({ project_item_id: p.project_item_id, quantity_failed: failed[p.project_item_id] })),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-label="Print results" onClick={e => { e.stopPropagation(); onClose(); }}
         style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'grid', placeItems: 'center', zIndex: 50 }}>
      <div className="card" style={{ padding: 20, minWidth: 360, maxWidth: 520 }} onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Print results</div>
        <div className="tiny muted" style={{ marginBottom: 12 }}>
          What came off this plate? Failed parts go back into the queue on their own.
        </div>
        <div className="row gap-2" style={{ marginBottom: 10 }}>
          <button className="btn sm" onClick={() => fillAll(false)}>Nothing failed</button>
          <button className="btn sm" onClick={() => fillAll(true)}>Whole plate failed</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 90px 90px', gap: 8, alignItems: 'center' }}>
          <span className="tag-key">Part</span><span className="tag-key">Succeeded</span><span className="tag-key">Failed</span>
          {pending.parts.map(p => (
            <PartRow key={p.project_item_id} name={p.file_name} quantity={p.quantity} failed={failed[p.project_item_id] ?? 0}
                     onChange={n => setFailed({ ...failed, [p.project_item_id]: Math.min(p.quantity, Math.max(0, n)) })} />
          ))}
        </div>
        {error && <div style={{ color: 'var(--err)', fontSize: 13, marginTop: 10 }}>{error}</div>}
        <div className="row gap-2" style={{ justifyContent: 'flex-end', marginTop: 14 }}>
          <button className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={saving} onClick={submit}>
            {saving ? 'Saving…' : 'Clear plate'}
          </button>
        </div>
      </div>
    </div>
  );
}

function PartRow({ name, quantity, failed, onChange }: { name: string; quantity: number; failed: number; onChange: (n: number) => void }) {
  return (
    <>
      <span className="small">{name} <span className="muted">× {quantity}</span></span>
      <span className="small" data-testid={`succeeded-${name}`}>{quantity - failed}</span>
      <input className="input" type="number" min={0} max={quantity} aria-label={`Failed ${name}`} value={failed}
             onChange={e => onChange(Number(e.target.value))} style={{ width: 80 }} />
    </>
  );
}
