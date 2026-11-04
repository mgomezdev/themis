import { useCallback, useEffect, useState } from 'react';
import { listProjectFailures, resolveHeldParts, type ProjectFailure, type ProjectItem } from '../api/projects';

const RESOLUTION: Record<string, string> = {
  requeued: 'Back in the queue', held: 'Held', reprinted: 'Reprinted', dropped: 'Dropped',
};

/**
 * A project's failure log (BIZ-364): every reported failure with its printer and time and what happened to the failed parts,
 * plus the parts held after failing on too many printers, each with Reprint / Drop.
 */
export function FailureLogCard({ projectId, items, onChanged }: {
  projectId: number;
  items: ProjectItem[];
  onChanged?: () => void;
}) {
  const [log, setLog] = useState<ProjectFailure[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    listProjectFailures(projectId).then(l => setLog(Array.isArray(l) ? l : [])).catch(e => setError(String(e)));
  }, [projectId]);
  useEffect(load, [load]);

  const held = items.filter(i => (i.quantity_held ?? 0) > 0);
  if (log.length === 0 && held.length === 0) return null;

  async function act(itemId: number, action: 'reprint' | 'drop') {
    setBusy(itemId);
    setError(null);
    try { await resolveHeldParts(projectId, itemId, action); load(); onChanged?.(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="card" style={{ padding: 20 }} data-testid="failure-log">
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Failures</div>
      {error && <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 8 }}>{error}</div>}
      {held.length > 0 && (
        <div className="col gap-2" style={{ marginBottom: 14 }}>
          {held.map(i => (
            <div key={i.id} className="row gap-2 small" style={{ alignItems: 'center' }}>
              <span className="pill warn">Held</span>
              <span style={{ flex: 1 }}>{i.file_name} × {i.quantity_held}: failed on too many printers</span>
              <button className="btn sm" disabled={busy === i.id} onClick={() => act(i.id, 'reprint')}>Reprint</button>
              <button className="btn ghost sm" disabled={busy === i.id} onClick={() => act(i.id, 'drop')}>Drop</button>
            </div>
          ))}
        </div>
      )}
      {log.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 60px 140px 160px 130px', gap: 6, fontSize: 13 }}>
          <span className="tag-key">Part</span><span className="tag-key">Failed</span><span className="tag-key">Printer</span>
          <span className="tag-key">When</span><span className="tag-key">Status</span>
          {log.map(e => (
            <FailureRow key={e.id} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}

function FailureRow({ entry: e }: { entry: ProjectFailure }) {
  return (
    <>
      <span>{e.file_name ?? `Item ${e.project_item_id}`}</span>
      <span>{e.quantity_failed}</span>
      <span>{e.printer_name ?? (e.printer_id != null ? `Printer ${e.printer_id}` : '—')}</span>
      <span className="muted">{e.created_at ? new Date(e.created_at).toLocaleString() : '—'}</span>
      <span>{e.resolution ? (RESOLUTION[e.resolution] ?? e.resolution) : '—'}</span>
    </>
  );
}
