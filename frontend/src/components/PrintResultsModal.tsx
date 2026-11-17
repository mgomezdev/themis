import { useState } from 'react';
import { markPlateCleared, type PlateResults } from '../api/printers';

/**
 * Print results (BIZ-364), asked when a plate holding a project job is cleared: per part, how many failed (the rest succeeded).
 * Failed parts go back into their print group's queue on their own; one that keeps failing on different printers is held on
 * its project. Submitting clears the plate.
 */
export function PrintResultsModal({ printerId, pending, onClose, onDone, onSubmit }: {
  printerId?: string;
  pending: PlateResults;
  onClose: () => void;
  onDone: () => void;
  /** Late-report mode (History): save the counts here instead of clearing the printer's plate. */
  onSubmit?: (results: { project_item_id: number; quantity_failed: number }[]) => Promise<void>;
}) {
  // A failed print opens on "whole plate failed": the person adjusts down if some parts are usable.
  const [failed, setFailed] = useState<Record<number, number>>(
    Object.fromEntries(pending.parts.map(p => [p.project_item_id, pending.print_failed ? p.quantity : 0])));
  const [saving, setSaving] = useState(false);
  const isTest = pending.test_print_waiver_id != null;        // BIZ-365: a test print of a waived part
  const [clearIt, setClearIt] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fillAll = (all: boolean) => setFailed(Object.fromEntries(pending.parts.map(p => [p.project_item_id, all ? p.quantity : 0])));

  async function submit() {
    if (pending.job_id == null) return;
    setSaving(true);
    setError(null);
    try {
      const results = pending.parts.filter(p => failed[p.project_item_id] > 0)
        .map(p => ({ project_item_id: p.project_item_id, quantity_failed: failed[p.project_item_id] }));
      if (onSubmit) await onSubmit(results);
      else await markPlateCleared(printerId as string, {
        job_id: pending.job_id,
        ...(isTest ? { clear_waiver: clearIt && pending.parts.every(p => (failed[p.project_item_id] ?? 0) === 0) } : {}),
        results,
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div role="dialog" aria-label="Print results" onClick={e => e.stopPropagation()}   // a stray click keeps the counts
         style={{ position: 'fixed', inset: 0, background: 'var(--scrim)', display: 'grid', placeItems: 'center', zIndex: 50 }}>
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
                     onChange={n => setFailed({ ...failed, [p.project_item_id]: Math.min(p.quantity, Math.max(0, Math.trunc(n) || 0)) })} />
          ))}
        </div>
        {isTest && (
          <label className="row gap-2 small" style={{ alignItems: 'center', marginTop: 12 }}>
            <input type="checkbox" checked={clearIt} onChange={e => setClearIt(e.target.checked)} />
            This was a test print: clear the waiver if nothing failed
          </label>
        )}
        {pending.print_failed && (
          <div className="notice warn small" role="note" style={{ marginTop: 12 }}>
            This print ended in a failure — adjust if some parts are usable.
          </div>
        )}
        {error && <div style={{ color: 'var(--err)', fontSize: 13, marginTop: 10 }}>{error}</div>}
        <div className="row gap-2" style={{ justifyContent: 'flex-end', marginTop: 14 }}>
          <button className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={saving} onClick={submit}>
            {saving ? 'Saving…' : onSubmit ? 'Save results' : 'Clear plate'}
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
