import { useEffect, useState } from 'react';
import { getProject, type ProjectItem } from '../api/projects';
import { markJobOutcome } from '../api/queue';

interface Props {
  job: {
    id: number;
    project_id: number;
    project_item_quantities: Record<string, number> | null;
  };
  onClose: () => void;
  onSaved: () => void;
}

export function OutcomeModal({ job, onClose, onSaved }: Props) {
  const [items, setItems] = useState<ProjectItem[]>([]);
  const [failures, setFailures] = useState<Record<number, number>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getProject(job.project_id)
      .then(p => {
        const relevant = p.items.filter(
          it => job.project_item_quantities != null && String(it.id) in job.project_item_quantities,
        );
        setItems(relevant);
        const init: Record<number, number> = {};
        for (const it of relevant) init[it.id] = 0;
        setFailures(init);
      })
      .catch(e => setError(String(e)));
  }, [job.project_id, job.project_item_quantities]);

  function qtyOnPlate(itemId: number): number {
    return job.project_item_quantities?.[String(itemId)] ?? 0;
  }

  function markAllGood() {
    const next: Record<number, number> = {};
    for (const it of items) next[it.id] = 0;
    setFailures(next);
  }

  function markAllFailed() {
    const next: Record<number, number> = {};
    for (const it of items) next[it.id] = qtyOnPlate(it.id);
    setFailures(next);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload = items
        .filter(it => (failures[it.id] ?? 0) > 0)
        .map(it => ({ project_item_id: it.id, quantity_failed: failures[it.id] }));
      await markJobOutcome(job.id, payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="card" style={{ width: 480, maxWidth: '90vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Mark Job Outcome</h3>
          <button className="btn ghost icon sm" onClick={onClose}>&#x2715;</button>
        </div>

        {items.length === 0 && !error && (
          <div className="muted small">Loading…</div>
        )}

        {items.length > 0 && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-3)' }}>
                  <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 500 }}>Part</th>
                  <th style={{ padding: '6px 8px', textAlign: 'center', fontWeight: 500 }}>On plate</th>
                  <th style={{ padding: '6px 8px', textAlign: 'center', fontWeight: 500 }}>Failed</th>
                </tr>
              </thead>
              <tbody>
                {items.map(it => {
                  const onPlate = qtyOnPlate(it.id);
                  return (
                    <tr key={it.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', color: 'var(--text-1)' }}>{it.file_name}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'center', color: 'var(--text-2)' }}>{onPlate}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                        <input
                          type="number"
                          className="input"
                          min={0}
                          max={onPlate}
                          value={failures[it.id] ?? 0}
                          onChange={e => {
                            const v = Math.max(0, Math.min(onPlate, parseInt(e.target.value) || 0));
                            setFailures(prev => ({ ...prev, [it.id]: v }));
                          }}
                          style={{ width: 60, textAlign: 'center' }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm" onClick={markAllGood}>Mark All Good</button>
              <button className="btn sm" onClick={markAllFailed}>Mark All Failed</button>
            </div>
          </>
        )}

        {error && (
          <div style={{ fontSize: 12, color: 'var(--err)' }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn sm" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn primary sm" onClick={handleSave} disabled={saving || items.length === 0}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
