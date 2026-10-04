import { useEffect, useState } from 'react';
import { listJobs, setJobProject, type ApiJob } from '../api/queue';
import { useFiles } from '../api/files';

/** Pick existing jobs that belong to no project and attach them to `projectId`. */
export function LinkJobsModal({ projectId, projectName, onClose, onLinked }: {
  projectId: number;
  projectName: string;
  onClose: () => void;
  onLinked: () => void;
}) {
  const [jobs, setJobs] = useState<ApiJob[] | null>(null);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);
  const { files } = useFiles({});

  useEffect(() => {
    let alive = true;
    listJobs()
      .then(all => { if (alive) setJobs(all.filter(j => j.project_id == null).sort((a, b) => b.id - a.id)); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Could not load jobs'); });
    return () => { alive = false; };
  }, []);

  const fileName = (id: number) => files.find(f => f.id === id)?.original_filename ?? `file #${id}`;

  async function link(job: ApiJob) {
    setBusyId(job.id);
    setError('');
    try {
      await setJobProject(job.id, projectId);
      setJobs(prev => (prev ?? []).filter(j => j.id !== job.id));
      onLinked();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not link the job');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(2,6,16,0.65)', backdropFilter: 'blur(4px)',
      zIndex: 100, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" role="dialog" aria-label="Link existing jobs"
           style={{ width: 'min(560px, 100%)', maxHeight: '80vh', overflowY: 'auto', padding: 0, borderColor: 'var(--border-3)' }}>
        <div className="row between" style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-1)', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 600 }}>Link existing jobs</div>
            <div className="tiny muted">Jobs without a project, to add to {projectName}</div>
          </div>
          <button className="btn ghost sm" onClick={onClose}>Close</button>
        </div>
        <div style={{ padding: '8px 20px 16px' }}>
          {error && <div style={{ color: 'var(--err)', fontSize: 13, margin: '8px 0' }}>{error}</div>}
          {jobs === null && !error && <div className="muted small" style={{ padding: '12px 0' }}>Loading…</div>}
          {jobs !== null && jobs.length === 0 && (
            <div className="muted small" style={{ padding: '12px 0' }}>Every job already belongs to a project.</div>
          )}
          {(jobs ?? []).map(job => (
            <div key={job.id} className="row between" style={{ alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--border-1)' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  #{job.id} · {fileName(job.uploaded_file_id)} <span className="tiny muted">p{job.plate_number}</span>
                </div>
                <div className="tiny muted">{job.status}</div>
              </div>
              <button className="btn sm" disabled={busyId !== null} onClick={() => link(job)}>
                {busyId === job.id ? 'Linking…' : 'Link'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
