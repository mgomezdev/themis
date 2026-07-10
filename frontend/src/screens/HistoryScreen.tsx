import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import { StatusPill } from '../components/ui';
import type { StatusKey } from '../data/types';

interface HistoryJob {
  id: number;
  uploaded_file_id: number;
  plate_number: number;
  order_id: number | null;
  project_id: number | null;
  assigned_printer_id: number | null;
  status: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  file_name: string | null;
  printer_name: string | null;
  project_name: string | null;
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

export function HistoryScreen() {
  const [jobs, setJobs] = useState<HistoryJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch('/api/v1/jobs/history')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.statusText}`)))
      .then(data => { if (alive) { setJobs(data); setLoading(false); } })
      .catch(e => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, []);

  if (loading) return <div className="muted small" style={{ padding: 16 }}>Loading…</div>;
  if (error) return <div style={{ padding: 16, color: 'var(--err)', fontSize: 13 }}>{error}</div>;
  if (jobs.length === 0) {
    return <Empty icon={Icons.clock} title="No completed jobs yet." />;
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', color: 'var(--text-3)' }}>
            <th style={{ padding: '8px 12px', fontWeight: 500 }}>Date</th>
            <th style={{ padding: '8px 12px', fontWeight: 500 }}>File</th>
            <th style={{ padding: '8px 12px', fontWeight: 500 }}>Printer</th>
            <th style={{ padding: '8px 12px', fontWeight: 500 }}>Status</th>
            <th style={{ padding: '8px 12px', fontWeight: 500 }}>Project</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(j => (
            <tr key={j.id} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '8px 12px', color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                {fmtDate(j.completed_at ?? j.updated_at)}
              </td>
              <td style={{ padding: '8px 12px', color: 'var(--text-1)' }}>
                {j.file_name ?? `File ${j.uploaded_file_id}`}
                {j.plate_number > 1 && (
                  <span style={{ color: 'var(--text-3)', marginLeft: 4 }}>p{j.plate_number}</span>
                )}
              </td>
              <td style={{ padding: '8px 12px', color: 'var(--text-2)' }}>
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
