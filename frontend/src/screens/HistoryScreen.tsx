import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import { StatusPill } from '../components/ui';
import { PrintResultsModal } from '../components/PrintResultsModal';
import { getProject } from '../api/projects';
import { reportJobResults } from '../api/queue';
import type { PlateResults } from '../api/printers';
import type { StatusKey } from '../data/types';
import { apiFetch } from '../api/client';

interface HistoryJob {
  id: number;
  uploaded_file_id: number;
  plate_number: number;
  order_id: number | null;
  project_id: number | null;
  assigned_printer_id: number | null;
  status: string;
  outcome: string | null;
  project_item_quantities: Record<string, number> | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  file_name: string | null;
  printer_name: string | null;
  project_name: string | null;
  estimate_filament_grams: number | null;
  estimate_seconds: number | null;
  actual_filament_grams: number | null;
  actual_seconds: number | null;
}

function fmtTime(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

/** Report or correct what came off a finished project plate (loads the part names, then the shared results dialog). */
function LateReportModal({ job, onClose, onSaved }: { job: HistoryJob; onClose: () => void; onSaved: () => void }) {
  const [pending, setPending] = useState<PlateResults | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    if (job.project_id == null) return;
    getProject(job.project_id).then(proj => {
      const qty = job.project_item_quantities ?? {};
      setPending({
        job_id: job.id, project_id: job.project_id, test_print_waiver_id: null, print_failed: job.status === 'failed',
        parts: proj.items.filter(it => String(it.id) in qty)
          .map(it => ({ project_item_id: it.id, file_name: it.file_name, quantity: qty[String(it.id)] })),
      });
    }).catch(e => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [job]);
  if (loadError) return (
    <div role="alert" className="notice err small" style={{ margin: 12 }}>
      {loadError} <button className="btn ghost sm" onClick={onClose}>Close</button>
    </div>
  );
  if (!pending) return null;
  return <PrintResultsModal pending={pending} onClose={onClose} onDone={() => { onSaved(); onClose(); }}
                            onSubmit={async results => { await reportJobResults(job.id, results); }} />;
}

function OutcomeCell({ job: j, busy, onReport }: { job: HistoryJob; busy: boolean; onReport: () => void }) {
  if (j.outcome === 'reviewed') return <span style={{ color: 'var(--ok)', fontSize: 12, fontWeight: 500 }}>&#x2713; Reviewed</span>;
  if (j.outcome === 'assumed_failed') {
    return <span className="muted small" title="Cleared without results; its parts are being printed again">Assumed failed</span>;
  }
  const reportable = (j.status === 'complete' || j.status === 'failed') && j.project_id !== null && j.project_item_quantities !== null;
  if (!reportable) return <>—</>;
  if (j.outcome === 'assumed_good') {
    return (
      <span className="row gap-2" style={{ alignItems: 'center' }}>
        <span className="muted small" title="Cleared without results">Assumed OK</span>
        <button className="btn sm" disabled={busy} onClick={onReport}>Change</button>
      </span>
    );
  }
  return <button className="btn sm" disabled={busy} onClick={onReport}>Report results</button>;
}

export function HistoryScreen() {
  const [jobs, setJobs] = useState<HistoryJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [outcomingJob, setOutcomingJob] = useState<HistoryJob | null>(null);

  function fetchJobs(alive: { v: boolean }) {
    apiFetch('/api/v1/jobs/history')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.statusText}`)))
      .then(data => { if (alive.v) { setJobs(data); setLoading(false); } })
      .catch(e => { if (alive.v) { setError(String(e)); setLoading(false); } });
  }

  useEffect(() => {
    const alive = { v: true };
    fetchJobs(alive);
    return () => { alive.v = false; };
  }, []);

  if (loading) return <div className="muted small" style={{ padding: 16 }}>Loading…</div>;
  if (error) return <div style={{ padding: 16, color: 'var(--err)', fontSize: 13 }}>{error}</div>;
  if (jobs.length === 0) {
    return <Empty icon={Icons.clock} title="No completed jobs yet." />;
  }

  return (
    <>
      {outcomingJob && (
        <LateReportModal
          job={outcomingJob}
          onClose={() => setOutcomingJob(null)}
          onSaved={() => {
            const alive = { v: true };
            fetchJobs(alive);
          }}
        />
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-1)', textAlign: 'left', color: 'var(--text-3)' }}>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Date</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>File</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Printer</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Status</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Project</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Est. Filament</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Est. Time</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Act. Filament</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Act. Time</th>
              <th style={{ padding: '8px 12px', fontWeight: 500 }}>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(j => (
              <tr key={j.id} style={{ borderBottom: '1px solid var(--border-1)' }}>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                  {fmtDate(j.completed_at ?? j.updated_at)}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)' }}>
                  {j.file_name ?? `File ${j.uploaded_file_id}`}
                  {j.plate_number > 1 && (
                    <span style={{ color: 'var(--text-3)', marginLeft: 4 }}>p{j.plate_number}</span>
                  )}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)' }}>
                  {j.printer_name ?? '—'}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <StatusPill status={j.status as StatusKey} />
                </td>
                <td style={{ padding: '8px 12px' }}>
                  {j.project_id ? (
                    <Link to={`/projects/${j.project_id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                      {j.project_name ?? `Project ${j.project_id}`}
                    </Link>
                  ) : '—'}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                  {j.estimate_filament_grams !== null ? `${j.estimate_filament_grams.toFixed(1)} g` : '—'}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                  {j.estimate_seconds !== null ? fmtTime(Math.round(j.estimate_seconds / 60)) : '—'}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                  {j.actual_filament_grams !== null ? `${j.actual_filament_grams.toFixed(1)} g` : '—'}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                  {j.actual_seconds !== null ? fmtTime(Math.round(j.actual_seconds / 60)) : '—'}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <OutcomeCell job={j} busy={outcomingJob !== null} onReport={() => setOutcomingJob(j)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
