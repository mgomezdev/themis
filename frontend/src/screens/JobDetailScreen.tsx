import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { fmtTime } from '../data/helpers';
import { StatusPill, Progress, Kv } from '../components/ui';
import { Icons } from '../components/icons';
import { getJobDetails, cancelJob, unblockJob, plateThumbnailUrl, type ApiJobDetails, type ApiJobPrinterConfig } from '../api/queue';
import type { StatusKey } from '../data/types';

const BADGE: Record<string, string> = {
  elegoo_centauri: 'ECC',
  bambu: 'P1S',
};

function printerBadge(type: string): string {
  return BADGE[type] ?? type.slice(0, 3).toUpperCase();
}

function SliceStatus({ cfg }: { cfg: ApiJobPrinterConfig }) {
  if (cfg.slice_failed) {
    return (
      <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)' }}>
        <div style={{ color: 'var(--err)', fontSize: 12, fontWeight: 600, marginBottom: 2 }}>Slice failed</div>
        {cfg.slice_error && (
          <div className="mono tiny" style={{ color: 'var(--err)', opacity: 0.8, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{cfg.slice_error}</div>
        )}
      </div>
    );
  }
  return null;
}

function PrinterConfigCard({ cfg, isAssigned }: { cfg: ApiJobPrinterConfig; isAssigned: boolean }) {
  const badge = printerBadge(cfg.printer_type);
  return (
    <div style={{
      padding: 14, borderRadius: 10,
      background: isAssigned ? 'var(--bg-3)' : 'var(--bg-1)',
      border: `1px solid ${isAssigned ? 'var(--accent)' : 'var(--border-1)'}`,
    }}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className={`elig ${isAssigned ? 'on' : 'off'}`}
              style={isAssigned ? { background: 'rgba(59,130,246,0.2)' } : undefined}>
          {badge}
        </span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{cfg.printer_name}</div>
          <div className="tiny muted">{cfg.printer_type}</div>
        </div>
        {isAssigned && (
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', letterSpacing: '0.05em' }}>ASSIGNED</span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <div className="tag-key" style={{ marginBottom: 4 }}>Print profile</div>
          <div className="small" style={{ color: 'var(--text-1)' }}>{cfg.print_profile || '—'}</div>
        </div>
        <div>
          <div className="tag-key" style={{ marginBottom: 4 }}>Filament</div>
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            {cfg.filament_color && (
              <div style={{
                width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                background: cfg.filament_color,
                border: '1px solid rgba(255,255,255,0.15)',
              }} />
            )}
            <div className="col" style={{ minWidth: 0 }}>
              <div className="small" style={{ color: 'var(--text-1)' }}>{cfg.filament_type || '—'}</div>
              {cfg.filament_profile && (
                <div className="tiny muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {cfg.filament_profile}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <SliceStatus cfg={cfg} />
    </div>
  );
}

export function JobDetailScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const jobId = id ? Number(id) : null;

  const [job, setJob] = useState<ApiJobDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [unblocking, setUnblocking] = useState(false);

  useEffect(() => {
    if (jobId == null) return;
    let alive = true;
    setLoading(true);
    getJobDetails(jobId)
      .then(d => { if (alive) { setJob(d); setLoading(false); } })
      .catch(e => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [jobId]);

  async function handleUnblock() {
    if (!job || unblocking) return;
    setUnblocking(true);
    try {
      await unblockJob(job.id);
      navigate('/queue');
    } catch (e) {
      setError(`Unblock failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setUnblocking(false);
    }
  }

  async function handleCancel() {
    if (!job || cancelling) return;
    setCancelling(true);
    try {
      await cancelJob(job.id);
      navigate(-1);
    } catch (e) {
      setError(`Cancel failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCancelling(false);
    }
  }

  if (loading) {
    return (
      <div className="col" style={{ alignItems: 'center', padding: '60px 20px', color: 'var(--text-3)' }}>
        <div className="tiny muted">Loading…</div>
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="col gap-3">
        <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }} onClick={() => navigate(-1)}>
          {Icons.chevL} Back
        </button>
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>
          {error ?? 'Job not found'}
        </div>
      </div>
    );
  }

  const isActive = job.status === 'printing' || job.status === 'paused';
  const isBlocked = job.status === 'blocked';
  const cancellable = ['queued', 'slicing', 'uploading', 'printing', 'paused', 'failed', 'blocked'].includes(job.status);
  const thumbUrl = plateThumbnailUrl(job.uploaded_file_id, job.plate?.thumbnail_path);

  return (
    <div className="col gap-4">
      {/* Breadcrumb */}
      <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }} onClick={() => navigate(-1)}>
        {Icons.chevL} Back
      </button>

      {error && (
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>{error}</div>
      )}

      <div className="layout-main-sidebar" style={{ gridTemplateColumns: 'minmax(0,1fr) 300px' }}>
        {/* Main column */}
        <div className="col gap-4">
          {/* Header card with thumbnail */}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{
              width: '100%', height: 200, position: 'relative',
              background: 'linear-gradient(135deg, #1e3a6e, #3b82f6)',
              display: 'grid', placeItems: 'center',
              overflow: 'hidden',
            }}>
              {thumbUrl ? (
                <img src={thumbUrl} alt={`Plate ${job.plate_number}`}
                     style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
              ) : (
                <span style={{ color: 'rgba(255,255,255,0.3)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
                  No preview
                </span>
              )}
              {job.status === 'failed' && (
                <div style={{
                  position: 'absolute', bottom: 10, left: 10,
                  padding: '3px 10px', borderRadius: 6,
                  background: 'rgba(239,68,68,0.85)', color: 'white',
                  fontSize: 11, fontWeight: 700, letterSpacing: '0.05em',
                }}>FAILED</div>
              )}
            </div>

            <div style={{ padding: 18 }}>
              <div className="row between" style={{ alignItems: 'flex-start', marginBottom: 14 }}>
                <div>
                  <div className="mono tiny muted" style={{ marginBottom: 4 }}>Job #{job.id}</div>
                  <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>
                    Plate {job.plate_number}
                    {job.file && (
                      <span className="muted" style={{ fontWeight: 400, fontSize: 14, marginLeft: 8 }}>
                        · {job.file.original_filename}
                      </span>
                    )}
                  </div>
                </div>
                <StatusPill status={job.status as StatusKey} />
              </div>

              {isActive && job.plate && (
                <div style={{ marginBottom: 14 }}>
                  <Progress value={0} large />
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    Live progress visible in the{' '}
                    <button className="btn ghost sm" style={{ padding: '0 4px', display: 'inline' }}
                            onClick={() => navigate('/queue')}>
                      Job queue
                    </button>
                  </div>
                </div>
              )}

              {job.block_reason && (
                <div style={{
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: job.status === 'failed' ? 'rgba(239,68,68,0.1)' : 'rgba(251,191,36,0.1)',
                  border: `1px solid ${job.status === 'failed' ? 'rgba(239,68,68,0.3)' : 'rgba(251,191,36,0.3)'}`,
                  color: job.status === 'failed' ? 'var(--err)' : 'var(--warn)',
                  fontSize: 13
                }}>
                  {job.status === 'failed' ? 'Failure Reason: ' : 'Blocked: '}{job.block_reason}
                </div>
              )}

              <div className="row gap-5" style={{ flexWrap: 'wrap' }}>
                {(job.estimated_seconds != null || job.plate?.estimated_time != null) && (
                  <Kv k="Est. print" v={
                    <span className="num small">
                      {fmtTime(Math.round((job.estimated_seconds ?? job.plate!.estimated_time!) / 60))}
                      {job.estimated_seconds != null && <span className="muted tiny" style={{ marginLeft: 4 }}>actual</span>}
                    </span>
                  } />
                )}
                {(job.filament_grams != null || job.plate?.filament_g != null) && (
                  <Kv k="Filament" v={
                    <span className="num small">
                      {(job.filament_grams ?? job.plate!.filament_g!).toFixed(1)} g
                      {job.filament_grams != null && <span className="muted tiny" style={{ marginLeft: 4 }}>actual</span>}
                    </span>
                  } />
                )}
                {job.queue_position != null && (
                  <Kv k="Queue pos." v={<span className="num small">#{job.queue_position}</span>} />
                )}
                {job.assigned_printer && (
                  <Kv k="Printing on" v={
                    <span className="small">
                      <span className={`elig on`} style={{ fontSize: 10, marginRight: 4 }}>
                        {printerBadge(job.assigned_printer.printer_type)}
                      </span>
                      {job.assigned_printer.name}
                    </span>
                  } />
                )}
              </div>
            </div>
          </div>

          {/* Printer configs */}
          {job.printer_configs.length > 0 && (
            <div className="card" style={{ padding: 20 }}>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 14 }}>
                Slicing configuration
                <span className="muted small" style={{ fontWeight: 400, marginLeft: 8 }}>
                  {job.printer_configs.length} eligible printer{job.printer_configs.length === 1 ? '' : 's'}
                </span>
              </div>
              <div className="col gap-3">
                {job.printer_configs.map(cfg => (
                  <PrinterConfigCard
                    key={cfg.printer_id}
                    cfg={cfg}
                    isAssigned={cfg.printer_id === job.assigned_printer_id}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="col gap-4">
          <div className="card" style={{ padding: 18 }}>
            <div className="tag-key" style={{ marginBottom: 12 }}>Timeline</div>
            <div className="col gap-2">
              <div className="row between">
                <span className="small muted">Created</span>
                <span className="num small">{job.created_at.slice(0, 10)}</span>
              </div>
              <div className="row between">
                <span className="small muted">Updated</span>
                <span className="num small">{job.updated_at.slice(0, 10)}</span>
              </div>
              <div className="row between">
                <span className="small muted">Status</span>
                <StatusPill status={job.status as StatusKey} />
              </div>
            </div>
          </div>

          {job.order_id != null && (
            <div className="card" style={{ padding: 18 }}>
              <div className="tag-key" style={{ marginBottom: 8 }}>Order</div>
              <button
                className="btn ghost sm"
                style={{ width: '100%', justifyContent: 'flex-start' }}
                onClick={() => navigate('/orders')}
              >
                {Icons.orders} View order #{job.order_id}
              </button>
            </div>
          )}

          {(isBlocked || job.status === 'failed' || job.status === 'queued') && (
            <div className="card" style={{ padding: 18 }}>
              <button
                className="btn sm"
                style={{ width: '100%' }}
                onClick={() => navigate(`/jobs/${job.id}/edit`)}
              >
                {Icons.copy} Edit slicer settings
              </button>
            </div>
          )}

          {isBlocked && (
            <div className="card" style={{ padding: 18 }}>
              <button
                className="btn primary sm"
                style={{ width: '100%' }}
                disabled={unblocking}
                onClick={handleUnblock}
              >
                {Icons.refresh} Unblock — retry at top of queue
              </button>
            </div>
          )}

          {cancellable && (
            <div className="card" style={{ padding: 18 }}>
              <button
                className="btn ghost sm"
                style={{ width: '100%', color: 'var(--err)' }}
                disabled={cancelling}
                onClick={handleCancel}
              >
                {Icons.trash} {job.status === 'failed' ? 'Remove failed job' : 'Remove from queue'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
