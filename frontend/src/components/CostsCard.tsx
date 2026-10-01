import { useCallback, useEffect, useState } from 'react';
import { fmtDate, fmtMoney } from '../data/helpers';
import { addLabor, deleteLabor, fmtMinutes, listLabor, type ProjectLabor } from '../api/costs';
import type { ProjectCosts } from '../api/projects';

const todayLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const ROWS: { key: 'filament' | 'machine' | 'labour' | 'parts'; label: string; hint: (c: ProjectCosts) => string }[] = [
  { key: 'filament', label: 'Filament', hint: () => 'entered per job' },
  { key: 'machine', label: 'Machine time', hint: c => `${c.machine_hours} h of completed prints` },
  { key: 'labour', label: 'Labour', hint: c => `${c.labour_hours} h logged` },
  { key: 'parts', label: 'Parts', hint: () => 'quantity × unit cost' },
];

/** What a project cost to make: filament + machine + labour + parts, plus the labour log. */
export function CostsCard({ projectId, costs, onChanged }: { projectId: number; costs: ProjectCosts; onChanged: () => void }) {
  const [labor, setLabor] = useState<ProjectLabor[] | null>(null);
  const [minutes, setMinutes] = useState('');
  const [loggedOn, setLoggedOn] = useState(todayLocal);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    listLabor(projectId).then(rows => setLabor(Array.isArray(rows) ? rows : [])).catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [projectId]);
  useEffect(() => { load(); }, [load]);

  const mins = Number(minutes);
  const canAdd = !busy && Number.isInteger(mins) && mins > 0 && !!loggedOn;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!canAdd) return;
    setBusy(true); setError('');
    try {
      await addLabor(projectId, { minutes: mins, logged_on: loggedOn, note: note.trim() || null });
      setMinutes(''); setNote('');
      load(); onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  }

  async function remove(l: ProjectLabor) {
    setError('');
    try { await deleteLabor(projectId, l.id); load(); onChanged(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }

  return (
    <div className="card" style={{ padding: 20 }} data-testid="costs-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>Expenses</div>
        <div className="small muted">At current rates (Settings → Costs) — changing a rate re-prices past jobs.</div>
      </div>

      <table className="tbl" style={{ marginBottom: 14 }}>
        <tbody>
          {ROWS.map(r => (
            <tr key={r.key} data-testid={`cost-${r.key}`}>
              <td>{r.label}</td>
              <td className="muted small">{r.hint(costs)}</td>
              <td style={{ textAlign: 'right' }}>{fmtMoney(costs[r.key])}</td>
            </tr>
          ))}
          <tr data-testid="cost-total" style={{ fontWeight: 600 }}>
            <td>Total</td><td /><td style={{ textAlign: 'right' }}>{fmtMoney(costs.total)}</td>
          </tr>
        </tbody>
      </table>

      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)', marginBottom: 6 }}>Labour log</div>
      {labor !== null && labor.length === 0 ? (
        <div style={{ color: 'var(--text-4)', fontSize: 13, marginBottom: 10 }}>No labour logged yet.</div>
      ) : (
        <div style={{ overflowX: 'auto', marginBottom: 10 }}>
          <table className="tbl">
            <tbody>
              {(labor ?? []).map(l => (
                <tr key={l.id} data-testid={`labor-${l.id}`}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(l.logged_on)}</td>
                  <td>{fmtMinutes(l.minutes)}</td>
                  <td className="muted">{l.note ?? ''}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="btn ghost sm" aria-label={`Delete ${fmtMinutes(l.minutes)} labour entry`} onClick={() => remove(l)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form onSubmit={add} className="row gap-2" style={{ flexWrap: 'wrap', alignItems: 'end' }}>
        <label className="col" style={{ gap: 4 }}>
          <span className="label">Minutes</span>
          <input className="input" type="number" min="1" step="1" placeholder="45" style={{ width: 90 }}
                 value={minutes} onChange={e => setMinutes(e.target.value)} aria-label="Labour minutes" />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="label">Date</span>
          <input className="input" type="date" value={loggedOn} max={todayLocal()}
                 onChange={e => setLoggedOn(e.target.value)} aria-label="Labour date" />
        </label>
        <label className="col" style={{ gap: 4, flex: '1 1 160px' }}>
          <span className="label">What</span>
          <input className="input" placeholder="e.g. post-processing" value={note} maxLength={500}
                 onChange={e => setNote(e.target.value)} aria-label="Labour note" />
        </label>
        <button className="btn primary sm" type="submit" disabled={!canAdd}>Log time</button>
      </form>
      {error && <div role="alert" style={{ color: 'var(--err)', fontSize: 12, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
