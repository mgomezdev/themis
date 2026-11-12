import { useCallback, useEffect, useState } from 'react';
import { clearAllWaivers, clearWaiver, listWaivers, testPrintWaiver, type PartWaiver } from '../api/printers';

/**
 * The parts a printer keeps failing and no longer gets (BIZ-365), with its flag / stop thresholds. Clear restores the printer
 * for a part; Test print sends one copy to this printer before anything else, and its plate-clear dialog offers to clear the
 * waiver. Completing maintenance does not clear waivers.
 */
export function WaivedPartsCard({ printerId, flagAt, stopAt, onThresholds }: {
  printerId: number;
  flagAt: number;
  stopAt: number;
  onThresholds: (v: { waiver_flag_at: number; waiver_stop_at: number }) => void;
}) {
  const [waivers, setWaivers] = useState<PartWaiver[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    listWaivers(printerId).then(w => setWaivers(Array.isArray(w) ? w : [])).catch(e => setError(String(e)));
  }, [printerId]);
  useEffect(load, [load]);

  async function run(fn: () => Promise<unknown>, done?: string) {
    setError(null);
    setNote(null);
    try { await fn(); load(); if (done) setNote(done); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <div data-testid="waived-parts">
      <div className="label" style={{ marginBottom: 6 }}>Waived parts</div>
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        <label className="row gap-1 small">
          Flag at
          <input className="input" type="number" min={0} aria-label="Flag at waived parts" value={flagAt} style={{ width: 70 }}
                 onChange={e => onThresholds({ waiver_flag_at: Math.max(0, Math.trunc(Number(e.target.value)) || 0), waiver_stop_at: stopAt })} />
        </label>
        <label className="row gap-1 small">
          Stop taking jobs at
          <input className="input" type="number" min={0} aria-label="Stop at waived parts" value={stopAt} style={{ width: 70 }}
                 onChange={e => onThresholds({ waiver_flag_at: flagAt, waiver_stop_at: Math.max(0, Math.trunc(Number(e.target.value)) || 0) })} />
        </label>
        <span className="tiny muted">waived parts (0 = off)</span>
      </div>
      {error && <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 6 }}>{error}</div>}
      {note && <div className="tiny" style={{ color: 'var(--ok)', marginBottom: 6 }}>{note}</div>}
      {waivers.length === 0 ? (
        <div className="tiny muted">None: this printer takes every part.</div>
      ) : (
        <div className="col gap-2">
          {waivers.map(w => (
            <div key={w.id} className="row gap-2 small wrap">
              <span style={{ flex: 1, minWidth: 160 }}>
                {w.file_name ?? 'Part'} <span className="muted">· failed {w.failures}× here
                {w.last_failure_at ? `, last ${new Date(w.last_failure_at).toLocaleDateString()}` : ''}
                {w.projects?.length ? ` · ${w.projects.join(', ')}` : ''}</span>
              </span>
              <button className="btn sm" onClick={() => run(() => testPrintWaiver(printerId, w.id), 'Test print queued: it is this printer’s next print.')}>
                Test print
              </button>
              <button className="btn ghost sm" onClick={() => run(() => clearWaiver(printerId, w.id))}>Clear</button>
            </div>
          ))}
          {waivers.length > 1 && (
            <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }} onClick={() => run(() => clearAllWaivers(printerId))}>
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}
