import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Progress } from '../components/ui';
import { PrinterEligibilityPicker } from '../components/PrinterEligibilityPicker';
import {
  getProject, getProjectJobs, generateProject,
  type Project, type ProjectJob,
} from '../api/projects';

const STATUS_META: Record<string, { label: string; color: string }> = {
  queued:    { label: 'Queued',    color: 'var(--text-3)' },
  slicing:   { label: 'Slicing',  color: 'var(--accent)' },
  uploading: { label: 'Uploading',color: 'var(--accent)' },
  printing:  { label: 'Printing', color: 'var(--ok)' },
  paused:    { label: 'Paused',   color: 'var(--warn)' },
  blocked:   { label: 'Blocked',  color: 'var(--err)' },
  complete:  { label: 'Done',     color: 'var(--ok)' },
  cancelled: { label: 'Cancelled',color: 'var(--text-4)' },
};

function fmtDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function ProjectDetailScreen() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? parseInt(id) : null;
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<ProjectJob[]>([]);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number } | null>(null);
  const [showPrinterPicker, setShowPrinterPicker] = useState(false);
  const [eligiblePrinterIds, setEligiblePrinterIds] = useState<number[]>([]);

  const reload = useCallback(() => {
    if (!projectId) return;
    getProject(projectId).then(setProject).catch(console.error);
    getProjectJobs(projectId).then(setJobs).catch(console.error);
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  async function handleGenerate() {
    if (!projectId) return;
    setGenerating(true);
    setGenerateError('');
    setGenerateResult(null);
    setShowPrinterPicker(false);
    try {
      const result = await generateProject(projectId, eligiblePrinterIds);
      setGenerateResult({ jobCount: result.jobs.length });
      reload();
    } catch (e) {
      setGenerateError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  if (!project) {
    return <div style={{ padding: 24, color: 'var(--text-3)' }}>Loading…</div>;
  }

  const dueStr = fmtDate(project.due_date);
  const isOverdue = project.due_date ? new Date(project.due_date) < new Date() : false;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 900 }}>

      {/* ── Header card ─────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 20 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text-1)' }}>
                {project.name}
              </h2>
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '2px 7px', borderRadius: 10,
                background: 'var(--bg-3)', color: 'var(--text-4)',
              }}>
                #{project.id}
              </span>
              {project.order_type === 'customer' && (
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 10,
                  background: 'var(--bg-3)', color: 'var(--text-3)',
                  textTransform: 'uppercase', letterSpacing: '0.04em',
                }}>Customer</span>
              )}
              {project.on_hold && (
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 10,
                  background: 'rgba(239,160,0,0.15)', color: 'var(--warn)',
                  textTransform: 'uppercase', letterSpacing: '0.04em',
                }}>On hold</span>
              )}
            </div>
            {project.customer && (
              <div style={{ fontSize: 13, color: 'var(--text-3)', marginTop: 3 }}>
                {project.customer}
              </div>
            )}
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-4)', marginTop: 6 }}>
              {dueStr && (
                <span style={{ color: isOverdue ? 'var(--err)' : 'var(--text-3)' }}>
                  Due {dueStr}
                </span>
              )}
              {project.notes && <span>{project.notes}</span>}
              {project.source_layout_id != null && (
                <span title={`source_app: ${project.source_app ?? '?'}`}>
                  {Icons.link}
                  {' '}Ordinus layout #{project.source_layout_id}
                  {project.source_user ? ` · ${project.source_user}` : ''}
                </span>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="btn sm" onClick={() => navigate(`/projects/${project.id}/edit`)}>
              Edit
            </button>
            <button
              className="btn primary sm"
              onClick={() => { setShowPrinterPicker(v => !v); setGenerateError(''); setGenerateResult(null); }}
              disabled={generating || project.items.length === 0}
            >
              {generating ? 'Generating…' : 'Generate…'}
            </button>
          </div>
        </div>

        {/* Job progress bar */}
        {project.jobs_total > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4,
                          fontSize: 12, color: 'var(--text-3)' }}>
              <span>{project.jobs_complete} / {project.jobs_total} jobs complete</span>
              <span style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-end', fontSize: 11 }}>
                <span style={{ color: 'var(--text-3)' }}>
                  Est. total:{' '}
                  {project.estimate_filament_grams_total != null
                    ? `${project.estimate_filament_grams_total.toFixed(1)} g`
                    : '—'}
                  {' / '}
                  {project.estimate_seconds_total != null
                    ? fmtDuration(project.estimate_seconds_total)
                    : '—'}
                </span>
                <span style={{ color: 'var(--text-3)' }}>
                  Est. remaining:{' '}
                  {project.estimate_filament_grams_remaining != null
                    ? `${project.estimate_filament_grams_remaining.toFixed(1)} g`
                    : '—'}
                  {' / '}
                  {project.estimate_seconds_remaining != null
                    ? fmtDuration(project.estimate_seconds_remaining)
                    : '—'}
                </span>
                <span style={{ color: 'var(--text-3)' }}>
                  Actual:{' '}
                  {project.actual_filament_grams != null
                    ? `${project.actual_filament_grams.toFixed(1)} g`
                    : '—'}
                  {' / '}
                  {project.actual_seconds != null
                    ? fmtDuration(project.actual_seconds)
                    : '—'}
                </span>
              </span>
            </div>
            <Progress
              value={project.jobs_total > 0 ? (project.jobs_complete / project.jobs_total) * 100 : 0}
              tone={project.jobs_complete === project.jobs_total ? 'ok' : undefined}
            />
          </div>
        )}

        {showPrinterPicker && !generating && (
          <div style={{
            marginTop: 14, padding: '14px 16px',
            border: '1px solid var(--border)', borderRadius: 8,
            background: 'var(--bg-2)',
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)',
                          textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
              Eligible printers
            </div>
            <PrinterEligibilityPicker selected={eligiblePrinterIds} onChange={setEligiblePrinterIds} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn sm" onClick={() => setShowPrinterPicker(false)}>Cancel</button>
              <button className="btn primary sm" onClick={handleGenerate}>
                Generate
              </button>
            </div>
          </div>
        )}

        {generateResult && (
          <div style={{
            marginTop: 12, border: '1px solid var(--ok)', borderRadius: 8,
            padding: '10px 14px', background: 'oklch(55% 0.15 142 / 0.06)',
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <span style={{ color: 'var(--ok)' }}>{Icons.check}</span>
            <span style={{ fontSize: 13, flex: 1 }}>
              {generateResult.jobCount} job{generateResult.jobCount !== 1 ? 's' : ''} queued
            </span>
            <button className="btn sm" onClick={() => navigate('/queue')}>View Queue</button>
          </div>
        )}
        {generateError && (
          <div style={{
            marginTop: 12, display: 'flex', alignItems: 'center', gap: 8,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
            borderRadius: 6, padding: '10px 14px',
          }}>
            <span style={{ color: 'var(--err)' }}>{Icons.alert}</span>
            <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{generateError}</span>
          </div>
        )}
      </div>

      {/* ── Links ───────────────────────────────────────────────────────── */}
      {project.links && project.links.length > 0 && (
        <div className="card" style={{ padding: 20 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)', marginBottom: 10 }}>
            Links
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {project.links.map(link => (
              <div key={link.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ color: 'var(--text-4)', fontSize: 13 }}>{Icons.link}</span>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ fontSize: 13, color: 'var(--accent)', textDecoration: 'none' }}
                  onMouseOver={e => (e.currentTarget.style.textDecoration = 'underline')}
                  onMouseOut={e => (e.currentTarget.style.textDecoration = 'none')}
                >
                  {link.label || link.url}
                </a>
                {link.label && (
                  <span style={{ fontSize: 11, color: 'var(--text-4)' }}>{link.url}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Parts ───────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>
          Parts ({project.items.length})
        </div>
        {project.items.length === 0 ? (
          <div style={{ color: 'var(--text-4)', fontSize: 13 }}>
            No parts — edit the project to add parts.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 50px 160px 110px',
              gap: 8, padding: '0 4px 6px',
              fontSize: 11, fontWeight: 500, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: '0.05em',
            }}>
              <span>File</span><span>Qty</span><span>Filament</span><span>Progress</span>
            </div>
            {project.items.map(item => {
              const filamentLabel = item.filament_id != null
                ? `Spoolman #${item.filament_id}`
                : `${item.filament_type} / ${item.filament_color}`;
              const done = item.quantity_completed;
              const failed = item.quantity_failed;
              return (
                <div key={item.id} style={{
                  display: 'grid', gridTemplateColumns: '1fr 50px 160px 110px',
                  gap: 8, alignItems: 'center', padding: '7px 4px',
                  borderTop: '1px solid var(--border)',
                }}>
                  <span style={{
                    fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap', color: 'var(--text-1)',
                  }} title={item.file_name}>
                    {item.file_name}
                  </span>
                  <span style={{ fontSize: 13, color: 'var(--text-2)' }}>×{item.quantity}</span>
                  <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{filamentLabel}</span>
                  <span style={{
                    fontSize: 11,
                    color: failed > 0 ? 'var(--err)' : done > 0 ? 'var(--ok)' : 'var(--text-4)',
                  }}>
                    {done > 0 || failed > 0
                      ? `${done}/${item.quantity}${failed > 0 ? ` · ${failed} failed` : ''}`
                      : '—'}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Jobs ────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>
          Jobs ({jobs.length})
        </div>
        {jobs.length === 0 ? (
          <div style={{ color: 'var(--text-4)', fontSize: 13 }}>
            No jobs yet — click Generate to create print jobs.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '44px 1fr 90px 70px 72px',
              gap: 8, padding: '0 4px 6px',
              fontSize: 11, fontWeight: 500, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: '0.05em',
            }}>
              <span>#</span><span>File</span><span>Status</span><span>Parts</span><span />
            </div>
            {jobs.map(job => {
              const st = STATUS_META[job.status] ?? { label: job.status, color: 'var(--text-3)' };
              return (
                <div key={job.id} style={{
                  display: 'grid', gridTemplateColumns: '44px 1fr 90px 70px 72px',
                  gap: 8, alignItems: 'center', padding: '8px 4px',
                  borderTop: '1px solid var(--border)',
                }}>
                  <span style={{ fontSize: 12, color: 'var(--text-4)' }}>#{job.id}</span>
                  <span style={{
                    fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    color: 'var(--text-1)',
                  }} title={job.file_name ?? undefined}>
                    {job.file_name ?? '—'}
                    {' '}<span style={{ fontSize: 11, color: 'var(--text-4)' }}>p{job.plate_number}</span>
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 500, color: st.color }}>{st.label}</span>
                  <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                    {job.total_parts > 0 ? `${job.total_parts}` : '—'}
                  </span>
                  <button
                    className="btn ghost sm"
                    style={{ fontSize: 11 }}
                    onClick={() => navigate(`/jobs/${job.id}`)}
                  >
                    Details
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

    </div>
  );
}
