import React, { useCallback, useEffect, useState } from 'react';
import { clearApiKey } from '../auth/apiKeyStore';
import {
  acceptQuote, createDraft, listMyProjects, updateDraft, uploadToDraft, type PortalProject,
} from '../api/customers';
import { methodLabel } from '../api/payments';
import { fmtDate, fmtMoney } from '../data/helpers';

const STAGE_LABEL: Record<string, string> = { draft: 'Draft', planning: 'Planning', queued: 'In production' };

function signOut() {
  clearApiKey();
  window.location.reload();
}

function DraftEditor({ project, onSaved }: { project: PortalProject; onSaved: (p: PortalProject) => void }) {
  const [name, setName] = useState(project.name);
  const [notes, setNotes] = useState(project.notes ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setName(project.name); setNotes(project.notes ?? ''); }, [project.id, project.name, project.notes]);

  async function run(fn: () => Promise<PortalProject>) {
    setBusy(true);
    setError(null);
    try { onSaved(await fn()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="col gap-2" style={{ marginTop: 12 }}>
      <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="Project name" />
      <textarea className="input" rows={4} value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="Describe what you need" />
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        <button className="btn primary sm" disabled={busy || !name.trim()}
                onClick={() => run(() => updateDraft(project.id, { name, notes }))}>Save</button>
        <label className="btn sm" style={{ cursor: busy ? 'default' : 'pointer' }}>
          Upload model (.stl / .3mf)
          <input type="file" accept=".stl,.3mf" hidden disabled={busy}
                 onChange={e => {
                   const f = e.target.files?.[0];
                   e.target.value = '';
                   if (f) run(() => uploadToDraft(project.id, f));
                 }} />
        </label>
        {error && <span className="small" style={{ color: 'var(--err)' }}>{error}</span>}
      </div>
    </div>
  );
}

/** The customer's view of what a project costs, what they've paid and what's left. Shown only once staff allow it. */
function QuoteCard({ project, onChanged }: { project: PortalProject; onChanged: (p: PortalProject) => void }) {
  const q = project.quote;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!q) return null;

  async function accept() {
    setBusy(true); setError(null);
    try { onChanged(await acceptQuote(project.id)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div data-testid="quote" style={{ marginTop: 16 }}>
      <div className="tag-key">Quote</div>
      <div className="row gap-4" style={{ marginTop: 6, flexWrap: 'wrap' }}>
        <div><div className="small muted">Price</div><div className="num" style={{ fontSize: 18 }}>{fmtMoney(q.price)}</div></div>
        <div><div className="small muted">Paid</div><div className="num" style={{ fontSize: 18 }}>{fmtMoney(q.paid)}</div></div>
        <div>
          <div className="small muted">Balance</div>
          <div className="num" data-testid="quote-balance" style={{ fontSize: 18, color: q.balance > 0 ? 'var(--warn)' : 'var(--ok)' }}>
            {q.balance > 0 ? fmtMoney(q.balance) : 'Paid in full'}
          </div>
        </div>
      </div>

      {q.payments.length > 0 && (
        <table className="small" style={{ width: '100%', marginTop: 8 }}>
          <thead><tr><th align="left">Received</th><th align="left">Method</th><th align="right">Amount</th></tr></thead>
          <tbody>
            {q.payments.map(p => (
              <tr key={p.id}><td>{fmtDate(p.received_on)}</td><td>{methodLabel(p.method)}</td><td align="right">{fmtMoney(p.amount)}</td></tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row gap-2" style={{ marginTop: 10, alignItems: 'center' }}>
        {q.accepted_at ? (
          <span className="small" style={{ color: 'var(--ok)' }}>
            You accepted this quote on {new Date(q.accepted_at).toLocaleDateString()}.
          </span>
        ) : (
          <button className="btn primary sm" disabled={busy} onClick={accept}>Accept quote</button>
        )}
        {error && <span className="small" style={{ color: 'var(--err)' }}>{error}</span>}
      </div>
    </div>
  );
}

function ProjectDetail({ project, onChanged }: { project: PortalProject; onChanged: (p: PortalProject) => void }) {
  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="row between" style={{ alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{project.name}</h2>
        <span className="pill">{STAGE_LABEL[project.stage] ?? project.stage}</span>
      </div>
      {project.stage === 'draft' ? (
        <DraftEditor project={project} onSaved={onChanged} />
      ) : (
        project.notes && <p className="small muted" style={{ whiteSpace: 'pre-wrap' }}>{project.notes}</p>
      )}

      <QuoteCard project={project} onChanged={onChanged} />

      <div style={{ marginTop: 16 }}>
        <div className="tag-key">Files</div>
        {project.items.length === 0 ? <div className="small muted">None yet</div> : (
          <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {project.items.map(i => <li key={i.id}>{i.filename} × {i.quantity}</li>)}
          </ul>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="tag-key">Jobs ({project.jobs_complete}/{project.jobs_total} complete)</div>
        {project.jobs.length === 0 ? <div className="small muted">No jobs yet</div> : (
          <table className="small" style={{ width: '100%', marginTop: 4 }}>
            <thead><tr><th align="left">Job</th><th align="left">Status</th><th align="left">Completed</th></tr></thead>
            <tbody>
              {project.jobs.map(j => (
                <tr key={j.id}>
                  <td>#{j.id} (plate {j.plate_number})</td>
                  <td>{j.status}</td>
                  <td>{j.completed_at ? new Date(j.completed_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function CustomerPortal() {
  const [projects, setProjects] = useState<PortalProject[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newName, setNewName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listMyProjects().then(setProjects).catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(load, [load]);

  function replace(p: PortalProject) {
    setProjects(prev => prev ? [p, ...prev.filter(x => x.id !== p.id)] : [p]);
    setSelectedId(p.id);
  }

  async function submitNew(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    try {
      replace(await createDraft({ name: newName.trim() }));
      setNewName('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const selected = projects?.find(p => p.id === selectedId) ?? null;
  // Money overview across the projects whose price staff have made visible (others contribute nothing).
  const quoted = (projects ?? []).filter(p => p.quote);
  const owed = quoted.reduce((sum, p) => sum + (p.quote?.balance ?? 0), 0);
  const owing = quoted.filter(p => (p.quote?.balance ?? 0) > 0).length;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: 24 }}>
      <div className="row between" style={{ alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>My projects</h1>
        <button className="btn ghost sm" onClick={signOut}>Sign out</button>
      </div>
      {error && <div className="small" style={{ color: 'var(--err)', marginBottom: 12 }}>{error}</div>}

      {quoted.length > 0 && (
        <div className="card" data-testid="balance-overview" style={{ padding: '12px 16px', marginBottom: 16 }}>
          <div className="small muted">Outstanding balance</div>
          <div className="num" style={{ fontSize: 22, color: owed > 0 ? 'var(--warn)' : 'var(--ok)' }}>
            {owed > 0 ? fmtMoney(owed) : 'All paid up'}
          </div>
          {owed > 0 && <div className="small muted">across {owing} {owing === 1 ? 'project' : 'projects'}</div>}
        </div>
      )}

      <form onSubmit={submitNew} className="row gap-2" style={{ marginBottom: 16 }}>
        <input className="input" value={newName} onChange={e => setNewName(e.target.value)}
               placeholder="New project request name" style={{ flex: 1 }} />
        <button className="btn primary sm" type="submit" disabled={!newName.trim()}>New request</button>
      </form>

      <div className="row gap-3" style={{ alignItems: 'flex-start' }}>
        <div className="col gap-2" style={{ width: 280, flexShrink: 0 }}>
          {projects === null ? <div className="small muted">Loading…</div>
            : projects.length === 0 ? <div className="small muted">No projects yet</div>
            : projects.map(p => (
              <button key={p.id} className="card" onClick={() => setSelectedId(p.id)}
                      style={{ padding: 12, textAlign: 'left', cursor: 'pointer',
                               borderColor: p.id === selectedId ? 'var(--accent)' : undefined }}>
                <div style={{ fontWeight: 500 }}>{p.name}</div>
                <div className="tiny muted">
                  {STAGE_LABEL[p.stage] ?? p.stage} · {p.jobs_complete}/{p.jobs_total} jobs
                  {p.quote && p.quote.balance > 0 ? ` · ${fmtMoney(p.quote.balance)} due` : ''}
                </div>
              </button>
            ))}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {selected ? <ProjectDetail project={selected} onChanged={replace} />
            : <div className="small muted">Select a project</div>}
        </div>
      </div>
    </div>
  );
}
