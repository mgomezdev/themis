import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setJobProject, type ApiJob } from '../api/queue';
import { createProject, getProjects, type Project } from '../api/projects';

/** Show which project a job belongs to, and link it to an existing / new project or unlink it. */
export function JobProjectControl({ jobId, projectId, projectName, onChanged }: {
  jobId: number;
  projectId: number | null | undefined;
  projectName?: string | null;
  onChanged: (job: ApiJob) => void;
}) {
  const navigate = useNavigate();
  const [picking, setPicking] = useState(false);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [choice, setChoice] = useState('');
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const created = useRef<{ name: string; id: number } | null>(null);   // survives a failed link so a retry doesn't duplicate

  async function run(action: () => Promise<ApiJob>) {
    setBusy(true);
    setError('');
    try {
      const job = await action();
      setPicking(false);
      setChoice('');
      setNewName('');
      onChanged(job);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not update the project link');
    } finally {
      setBusy(false);
    }
  }

  function openPicker() {
    setPicking(true);
    setError('');
    getProjects()
      .then(all => setProjects(all.filter(p => p.stage !== 'draft')))
      .catch(e => { setProjects([]); setError(e instanceof Error ? e.message : 'Could not load projects'); });
  }

  const createAndLink = () => run(async () => {
    const name = newName.trim();
    if (created.current?.name !== name) created.current = { name, id: (await createProject({ name })).id };
    const job = await setJobProject(jobId, created.current.id);
    created.current = null;
    return job;
  });

  return (
    <div className="col gap-2">
      {projectId != null ? (
        <div className="row between" style={{ alignItems: 'center' }}>
          <button className="btn ghost sm" style={{ minWidth: 0 }} onClick={() => navigate(`/projects/${projectId}`)}>
            {projectName ?? `Project #${projectId}`}
          </button>
          <button className="btn ghost sm" disabled={busy} onClick={() => run(() => setJobProject(jobId, null))}>
            Unlink
          </button>
        </div>
      ) : !picking ? (
        <button className="btn sm" style={{ width: '100%' }} onClick={openPicker}>Link to project…</button>
      ) : (
        <div className="col gap-2">
          <select aria-label="Existing project" value={choice} onChange={e => setChoice(e.target.value)}>
            <option value="">{projects === null ? 'Loading…' : 'Choose a project'}</option>
            {(projects ?? []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn primary sm" disabled={busy || !choice}
                  onClick={() => run(() => setJobProject(jobId, Number(choice)))}>
            Link
          </button>
          <input aria-label="New project name" placeholder="…or a new project name" value={newName}
                 onChange={e => setNewName(e.target.value)} />
          <button className="btn sm" disabled={busy || !newName.trim()} onClick={createAndLink}>
            Create & link
          </button>
          <button className="btn ghost sm" onClick={() => setPicking(false)}>Cancel</button>
        </div>
      )}
      {error && <div style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
    </div>
  );
}
