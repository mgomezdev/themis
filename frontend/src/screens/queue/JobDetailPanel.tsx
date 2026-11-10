// Queue: the open job's side panel (status, reasons, actions, verify slice).
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { fmtTime } from '../../data/helpers';
import { StatusPill, Progress } from '../../components/ui';
import { Icons } from '../../components/icons';
import {
  getSliceFailures,
  getJobDetails,
  verifySlice,
  plateThumbnailUrl,
  type ApiSliceFailure,
  type ApiJobPrinterConfig,
} from '../../api/queue';
import { JobProjectControl } from '../../components/JobProjectControl';
import type { StatusKey } from '../../data/types';
import { DisplayJob } from './model';

// ---- JobDetailPanel ----
export function JobDetailPanel({
  job,
  onClose,
  onCancel,
  onUnblock,
  onReorder,
  onProjectChanged,
}: {
  job: DisplayJob;
  onClose: () => void;
  onCancel: (jobId: number) => void;
  onUnblock: (jobId: number) => void;
  onReorder: (jobId: number, action: 'promote' | 'demote' | 'front' | 'back') => void;
  onProjectChanged: () => void;
}) {
  const navigate = useNavigate();
  const isActive = job.status === 'printing' || job.status === 'paused';
  const isFailed = job.status === 'failed';
  const isBlocked = job.status === 'blocked';
  const cancellable = ['queued', 'slicing', 'uploading', 'printing', 'paused', 'failed'].includes(job.status);
  const thumbUrl = plateThumbnailUrl(job.fileId, job.thumbnailPath);

  const [sliceFailures, setSliceFailures] = React.useState<ApiSliceFailure[]>([]);
  React.useEffect(() => {
    if (!isFailed) return;
    let alive = true;
    getSliceFailures(job.rawId)
      .then(d => { if (alive) setSliceFailures(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [job.rawId, isFailed]);

  const [verifyOpen, setVerifyOpen] = React.useState(false);
  const [verifyConfigs, setVerifyConfigs] = React.useState<ApiJobPrinterConfig[] | null>(null);
  const [verifyPrinterId, setVerifyPrinterId] = React.useState<number | null>(null);
  const [verifyRunning, setVerifyRunning] = React.useState(false);
  const [verifyResult, setVerifyResult] = React.useState<{ ok: boolean; error: string | null } | null>(null);

  React.useEffect(() => {
    if (!verifyOpen || verifyConfigs !== null) return;
    let alive = true;
    getJobDetails(job.rawId)
      .then(d => {
        if (!alive) return;
        setVerifyConfigs(d.printer_configs);
        if (d.printer_configs.length === 1) setVerifyPrinterId(d.printer_configs[0].printer_id);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [verifyOpen, job.rawId, verifyConfigs]);

  const runVerify = async () => {
    if (verifyPrinterId === null) return;
    setVerifyRunning(true);
    setVerifyResult(null);
    try {
      const result = await verifySlice(job.rawId, verifyPrinterId);
      setVerifyResult(result);
    } catch (e: unknown) {
      setVerifyResult({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setVerifyRunning(false);
    }
  };

  return (
    <div className="card" style={{ position: 'sticky', top: 0, padding: 0, height: 'fit-content', overflow: 'hidden' }}>
      {/* Thumbnail header */}
      <div style={{
        width: '100%', height: 180, position: 'relative',
        background: 'linear-gradient(135deg, var(--bg-2), var(--bg-3))',
        display: 'grid', placeItems: 'center',
        overflow: 'hidden',
      }}>
        {thumbUrl ? (
          <img src={thumbUrl} alt={job.plateName}
               style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block' }} />
        ) : (
          <span style={{ color: 'rgba(255,255,255,0.35)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
            No preview
          </span>
        )}
        <button
          className="btn ghost icon sm"
          onClick={onClose}
          style={{ position: 'absolute', top: 8, right: 8, background: 'rgba(0,0,0,0.4)', border: 'none' }}
        >
          {Icons.x}
        </button>
        {isFailed && (
          <div style={{
            position: 'absolute', bottom: 8, left: 8,
            padding: '3px 8px', borderRadius: 0,
            background: 'var(--err)', color: 'white',
            fontSize: 11, fontWeight: 600, letterSpacing: '0.04em',
          }}>
            FAILED
          </div>
        )}
      </div>

      <div style={{ padding: 18 }}>
        <div className="row between" style={{ alignItems: 'flex-start', marginBottom: 10 }}>
          <div>
            <div className="mono tiny muted" style={{ marginBottom: 2 }}>Job #{job.rawId}</div>
            <div style={{ fontSize: 15, fontWeight: 500 }}>{job.plateName}</div>
          </div>
          <StatusPill status={job.status as StatusKey} />
        </div>

        {isActive && (
          <div style={{ marginBottom: 14 }}>
            <Progress value={job.progress} large />
            <div className="row between" style={{ marginTop: 6 }}>
              <span className="tiny muted">
                {job.layer && (
                  <>layer{' '}
                  <span className="num" style={{ color: 'var(--text-2)' }}>
                    {job.layer.now}/{job.layer.total}
                  </span></>
                )}
              </span>
              <span className="tiny muted">
                {job.progress}% ·{' '}
                <span className="num">{fmtTime(Math.max(0, job.estTime - job.elapsed))}</span> left
              </span>
            </div>
          </div>
        )}

        <div className="divider" />

        <div className="col gap-2" style={{ marginBottom: 14 }}>
          {job.estTime > 0 && (
            <div className="row between">
              <span className="small muted">Est. print time</span>
              <span className="num small">{fmtTime(job.estTime)}</span>
            </div>
          )}
          {job.filamentG > 0 && (
            <div className="row between">
              <span className="small muted">Filament</span>
              <span className="num small">{job.filamentG.toFixed(1)} g</span>
            </div>
          )}
          {!isFailed && (
            <div className="row between">
              <span className="small muted">Slicing</span>
              <span className="small">{job.sliced ? 'Ready' : 'On claim'}</span>
            </div>
          )}
          {job.ordinalRank > 0 && (
            <div className="row between">
              <span className="small muted">Queue position</span>
              <span className="num small">#{job.ordinalRank}</span>
            </div>
          )}
        </div>

        {job.lowStockWarning && (
          <>
            <div className="divider" />
            <div style={{ marginBottom: 14, padding: '10px 12px', borderRadius: 0, background: 'var(--warn-bg)', border: '1px solid var(--warn-border)' }}>
              <div className="tiny" style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                Low filament
              </div>
              <div className="small" style={{ color: 'var(--text-2)', lineHeight: 1.5 }}>{job.lowStockWarning.message}</div>
            </div>
          </>
        )}

        {job.eligiblePrinters.length > 0 && (
          <>
            <div className="divider" />
            <div className="tag-key" style={{ marginBottom: 8 }}>Eligible printers</div>
            <div className="col gap-2" style={{ marginBottom: 14 }}>
              {job.eligiblePrinters.map(p => (
                <div key={p.id} className="row between" style={{
                  padding: '6px 10px', background: 'var(--bg-1)',
                  borderRadius: 0, border: '1px solid var(--border-1)',
                }}>
                  <div className="small">{p.name}</div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Block / Failure reason */}
        {(isBlocked || isFailed) && job.blockReason && (
          <>
            <div className="divider" />
            <div style={{ marginBottom: 14, padding: '10px 12px', borderRadius: 0, background: isFailed ? 'var(--err-bg)' : 'var(--warn-bg)', border: isFailed ? '1px solid var(--err-border)' : '1px solid var(--warn-border)' }}>
              <div className="tiny" style={{ fontWeight: 600, color: isFailed ? 'var(--err)' : 'var(--warn)', marginBottom: 4 }}>
                {isFailed ? 'Failure Reason' : 'Blocked'}
              </div>
              <div className="small" style={{ color: 'var(--text-2)', lineHeight: 1.5 }}>{job.blockReason}</div>
            </div>
          </>
        )}

        {/* Slice failures */}
        {isFailed && sliceFailures.length > 0 && (
          <>
            <div className="divider" />
            <div className="tag-key" style={{ marginBottom: 8 }}>Slice errors</div>
            <div className="col gap-2" style={{ marginBottom: 14 }}>
              {sliceFailures.map((f, i) => (
                <div key={i} style={{ padding: '10px 12px', borderRadius: 0, background: 'var(--err-bg)', border: '1px solid var(--err-border)' }}>
                  <div className="tiny" style={{ fontWeight: 600, color: 'var(--err)', marginBottom: 4 }}>
                    Printer {f.printer_id} · {f.print_profile}
                  </div>
                  {f.slice_error && (
                    <div className="mono tiny" style={{ color: 'var(--text-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 120, overflowY: 'auto' }}>
                      {f.slice_error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}

        {/* Verify slicing (debug) */}
        {!isActive && (
          <>
            <div className="divider" />
            <button
              className="btn ghost sm"
              style={{ width: '100%', marginBottom: verifyOpen ? 8 : 0, textAlign: 'left' }}
              onClick={() => { setVerifyOpen(o => !o); setVerifyResult(null); }}
            >
              {Icons.refresh} Verify slicing…
            </button>
            {verifyOpen && (
              <div className="col gap-2" style={{ marginBottom: 8 }}>
                <select
                  className="select"
                  value={verifyPrinterId ?? ''}
                  onChange={e => { setVerifyPrinterId(Number(e.target.value)); setVerifyResult(null); }}
                  disabled={verifyRunning}
                >
                  {verifyConfigs === null
                    ? <option value="">Loading…</option>
                    : verifyConfigs.map(c => (
                        <option key={c.printer_id} value={c.printer_id}>{c.printer_name}</option>
                      ))
                  }
                </select>
                <button
                  className="btn sm primary"
                  style={{ width: '100%' }}
                  disabled={verifyRunning || verifyPrinterId === null}
                  onClick={runVerify}
                >
                  <span style={verifyRunning ? { display: 'inline-flex', animation: 'spin 1s linear infinite' } : undefined}>
                    {Icons.refresh}
                  </span>
                  {verifyRunning ? ' Slicing…' : ' Run test slice'}
                </button>
                {verifyRunning && (
                  <div style={{
                    padding: '8px 12px', borderRadius: 0,
                    background: 'var(--warn-bg)',
                    border: '1px solid var(--warn-border)',
                    animation: 'pulse-soft 1.5s ease-in-out infinite',
                  }}>
                    <div className="tiny" style={{ color: 'var(--warn)', fontWeight: 500 }}>
                      Slicing in progress — this can take 30–90 seconds…
                    </div>
                  </div>
                )}
                {verifyResult && (
                  <div style={{
                    padding: '10px 12px', borderRadius: 0,
                    background: verifyResult.ok ? 'var(--ok-bg)' : 'var(--err-bg)',
                    border: `1px solid ${verifyResult.ok ? 'var(--ok-border)' : 'var(--err-border)'}`,
                  }}>
                    <div className="tiny" style={{
                      fontWeight: 600,
                      color: verifyResult.ok ? 'var(--ok)' : 'var(--err)',
                      marginBottom: verifyResult.error ? 4 : 0,
                    }}>
                      {verifyResult.ok ? '✓ Sliced OK' : '✗ Slice failed'}
                    </div>
                    {verifyResult.error && (
                      <div className="mono tiny" style={{
                        color: 'var(--text-2)', whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all', maxHeight: 160, overflowY: 'auto',
                      }}>
                        {verifyResult.error}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Edit settings */}
        {(isBlocked || isFailed || job.status === 'queued') && (
          <button
            className="btn sm"
            style={{ width: '100%', marginBottom: 8 }}
            onClick={() => navigate(`/jobs/${job.rawId}/edit`)}
          >
            {Icons.copy} Edit slicer settings
          </button>
        )}

        <div style={{ marginBottom: 8 }}>
          <div className="tag-key" style={{ marginBottom: 6 }}>Project</div>
          <JobProjectControl jobId={job.rawId} projectId={job.projectId} projectName={job.projectName} onChanged={onProjectChanged} />
        </div>

        {/* View full details */}
        <button
          className="btn ghost sm"
          style={{ width: '100%', marginBottom: 8 }}
          onClick={() => navigate(`/jobs/${job.rawId}`)}
        >
          View full details →
        </button>

        {isBlocked && (
          <button
            className="btn primary sm"
            style={{ width: '100%', marginBottom: 8 }}
            onClick={() => onUnblock(job.rawId)}
          >
            {Icons.refresh} Unblock — retry at top of queue
          </button>
        )}

        {(job.status === 'queued' || job.status === 'blocked') && (
          <>
            <div className="divider" />
            <div className="tag-key" style={{ marginBottom: 8 }}>Queue position</div>
            <div className="row gap-2" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
              <button className="btn ghost sm" style={{ flex: 1 }}
                      onClick={() => onReorder(job.rawId, 'front')}>⇤ Front</button>
              <button className="btn ghost sm" style={{ flex: 1 }}
                      onClick={() => onReorder(job.rawId, 'promote')}>↑ Up</button>
              <button className="btn ghost sm" style={{ flex: 1 }}
                      onClick={() => onReorder(job.rawId, 'demote')}>↓ Down</button>
              <button className="btn ghost sm" style={{ flex: 1 }}
                      onClick={() => onReorder(job.rawId, 'back')}>⇥ Back</button>
            </div>
          </>
        )}

        {cancellable && (
          <button
            className="btn ghost sm"
            style={{ width: '100%', color: 'var(--err)' }}
            onClick={() => onCancel(job.rawId)}
          >
            {Icons.trash} {isFailed ? 'Remove failed job' : 'Remove from queue'}
          </button>
        )}
      </div>
    </div>
  );
}
