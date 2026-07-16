import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fmtTime, matColor } from '../data/helpers';
import {
  StatusPill, Progress, EligibilityChips, MaterialChip, Empty, Kv,
} from '../components/ui';
import { Icons } from '../components/icons';
import { useQueue, useFilePlates, cancelJob, unblockJob, reorderJob, getSliceFailures, getJobDetails, verifySlice, plateThumbnailUrl, type ApiSliceFailure, type ApiJobPrinterConfig } from '../api/queue';
import { useFleetData } from '../api/fleet';
import type { StatusKey } from '../data/types';

// ---- DisplayJob: flattened shape for rendering ----
interface DisplayJob {
  id: string;
  rawId: number;
  fileName: string | null;
  plateName: string;
  status: string;
  blockReason: string | null;
  materials: string[];
  eligiblePrinters: Array<{ id: number; name: string }>;
  estTime: number;
  filamentG: number;
  elapsed: number;
  progress: number;
  layer: { now: number; total: number } | null;
  sliced: boolean;
  queuePosition: number;
  ordinalRank: number;
  fileId: number;
  thumbnailPath: string | null;
  printerName: string | null;
}

// ---- FilterChip ----
function FilterChip({
  active,
  children,
  onClick,
  ariaLabel,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
  ariaLabel?: string;
}) {
  return (
    <button
      aria-label={ariaLabel}
      onClick={onClick}
      className={`btn sm ${active ? 'primary' : ''}`}
      style={active ? undefined : { background: 'transparent', borderColor: 'var(--border-1)' }}
    >
      {children}
    </button>
  );
}

// ---- SummaryStat ----
function SummaryStat({
  label,
  value,
  sub,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  mono?: boolean;
}) {
  return (
    <div className="card" style={{ minWidth: 160, padding: '14px 16px' }}>
      <div className="tag-key">{label}</div>
      <div
        className="row gap-2"
        style={{ marginTop: 6, alignItems: 'baseline', whiteSpace: 'nowrap' }}
      >
        <div
          className={mono ? 'num' : ''}
          style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}
        >
          {value}
        </div>
      </div>
      {sub && (
        <div className="tiny muted" style={{ marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ---- JobCardRich helpers ----
function getErrorCategory(reason: string | null): string {
  if (!reason) return 'Unknown Error';
  const r = reason.toLowerCase();
  if (r.includes('slicing failed') || r.includes('slice failed')) return 'Slicing Error';
  if (r.includes('upload failed') || r.includes('upload reported failure')) return 'Upload Error';
  if (r.includes('start print failed') || r.includes('start print reported failure')) return 'Print Startup Error';
  return 'Queue Error';
}

function getErrorMessage(reason: string | null): string {
  if (!reason) return 'An unexpected error occurred. Click for details.';
  // Strip common prefixes if present to make the card clean
  let msg = reason;
  const prefixes = [
    'slicing failed: ',
    'slicing failed:',
    'gcode upload failed: ',
    'gcode upload failed:',
    'start print failed: ',
    'start print failed:',
  ];
  for (const prefix of prefixes) {
    if (msg.toLowerCase().startsWith(prefix)) {
      msg = msg.slice(prefix.length);
      break;
    }
  }
  return msg;
}

// ---- JobCardRich ----
function JobCardRich({
  job,
  position,
  selected,
  onClick,
  showStatus,
}: {
  job: DisplayJob;
  position: number;
  selected: boolean;
  onClick: () => void;
  showStatus: boolean;
}) {
  const isActive = job.status === 'printing' || job.status === 'paused';
  const isFailed = job.status === 'failed';
  const isBlocked = job.status === 'blocked';
  const thumbUrl = plateThumbnailUrl(job.fileId, job.thumbnailPath);

  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        padding: 0,
        cursor: 'pointer',
        overflow: 'hidden',
        borderColor: selected ? 'var(--accent)' : isFailed ? 'rgba(239,68,68,0.55)' : isBlocked ? 'rgba(251,191,36,0.6)' : undefined,
        boxShadow: selected ? 'inset 0 0 0 1px var(--accent)' : isFailed ? 'inset 0 0 0 1px rgba(239,68,68,0.15)' : isBlocked ? 'inset 0 0 0 1px rgba(251,191,36,0.12)' : undefined,
      }}
    >
      <div className="row gap-4" style={{ padding: 14 }}>
        {/* plate thumbnail */}
        <div
          style={{
            width: 80, height: 80, flexShrink: 0,
            background: `linear-gradient(135deg, var(--bg-2), var(--bg-3))`,
            borderRadius: 0,
            border: '1px solid var(--border-2)',
            display: 'grid', placeItems: 'center',
            overflow: 'hidden',
          }}
        >
          {thumbUrl ? (
            <img src={thumbUrl} alt={job.plateName}
                 style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : (
            <span style={{
              color: 'rgba(255,255,255,0.5)',
              fontFamily: 'var(--font-mono)', fontSize: 10,
            }}>#{position}</span>
          )}
        </div>

        {/* content */}
        <div className="col" style={{ flex: 1, minWidth: 0, gap: 8 }}>
          <div className="row between">
            <div>
              <div className="row gap-2" style={{ alignItems: 'baseline' }}>
                <span className="mono tiny muted">#{job.rawId}</span>
                <div style={{ fontSize: 15, fontWeight: 500 }}>{job.plateName}</div>
              </div>
              {job.fileName && (
                <div className="tiny muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {job.fileName}
                </div>
              )}
            </div>
            {(showStatus || isFailed) && (
              <div className="row gap-2">
                <StatusPill status={job.status as StatusKey} />
              </div>
            )}
          </div>

          <div className="row gap-5" style={{ marginTop: 4 }}>
            {job.materials.length > 0 && (
              <Kv k="Material" v={
                <div className="row gap-1">
                  {job.materials.map(m => <MaterialChip key={m} material={m} color={matColor(m)} />)}
                </div>
              } />
            )}
            {job.eligiblePrinters.length > 0 && (
              <Kv k="Eligible" v={<EligibilityChips ids={job.eligiblePrinters.map(p => p.name)} />} />
            )}
            {job.estTime > 0 && (
              <Kv k="Est. print" v={<span className="num">{fmtTime(job.estTime)}</span>} />
            )}
            {isActive ? (
              <Kv
                k="Remaining"
                v={
                  <span className="num" style={{ color: 'var(--accent-hi)' }}>
                    {fmtTime(Math.max(0, job.estTime - job.elapsed))}
                  </span>
                }
              />
            ) : null}
            {!isActive && !isFailed && (
              <Kv k="Slicing" v={
                isBlocked && job.blockReason?.toLowerCase().includes('slice')
                  ? <span style={{ color: 'var(--err)' }}>failed</span>
                  : isBlocked
                    ? <span style={{ color: 'var(--warn)' }}>blocked</span>
                    : job.sliced
                      ? 'ready'
                      : <span title="Will slice when a printer claims this job">on claim</span>
              } />
            )}
            {isActive && job.printerName && (
              <Kv k="Printer" v={<span className="small">{job.printerName}</span>} />
            )}
          </div>

          {isActive && (
            <div style={{ marginTop: 4 }}>
              <Progress value={job.progress} />
            </div>
          )}
        </div>
      </div>

      {/* Error / Blocked strip — visible without opening the panel */}
      {isBlocked && job.blockReason && (
        <div
          style={{
            padding: '10px 14px',
            borderTop: '1px solid rgba(251,191,36,0.3)',
            background: 'rgba(251,191,36,0.09)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          <div
            className="row gap-1"
            style={{
              color: 'var(--warn)',
              fontSize: 11,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              alignItems: 'center',
            }}
          >
            <span style={{ display: 'inline-flex', color: 'var(--warn)' }}>{Icons.alert}</span>
            <span>Blocked / Waiting</span>
          </div>
          <span className="tiny" style={{ color: 'var(--text-1)', lineHeight: 1.4, wordBreak: 'break-word' }}>
            {job.blockReason}
          </span>
        </div>
      )}
      {isFailed && (
        <div
          style={{
            padding: '10px 14px',
            borderTop: '1px solid rgba(239,68,68,0.3)',
            background: 'rgba(239,68,68,0.09)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          <div
            className="row gap-1"
            style={{
              color: 'var(--err)',
              fontSize: 11,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              alignItems: 'center',
            }}
          >
            <span style={{ display: 'inline-flex', color: 'var(--err)' }}>{Icons.alert}</span>
            <span>{getErrorCategory(job.blockReason)}</span>
          </div>
          <span className="tiny" style={{ color: 'var(--text-1)', lineHeight: 1.4, wordBreak: 'break-word' }}>
            {getErrorMessage(job.blockReason)}
          </span>
        </div>
      )}
    </div>
  );
}

// ---- JobDetailPanel ----
function JobDetailPanel({
  job,
  onClose,
  onCancel,
  onUnblock,
  onReorder,
}: {
  job: DisplayJob;
  onClose: () => void;
  onCancel: (jobId: number) => void;
  onUnblock: (jobId: number) => void;
  onReorder: (jobId: number, action: 'promote' | 'demote' | 'front' | 'back') => void;
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
            background: 'rgba(239,68,68,0.85)', color: 'white',
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
            <div style={{ marginBottom: 14, padding: '10px 12px', borderRadius: 0, background: isFailed ? 'rgba(239,68,68,0.08)' : 'rgba(251,191,36,0.1)', border: isFailed ? '1px solid rgba(239,68,68,0.25)' : '1px solid rgba(251,191,36,0.3)' }}>
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
                <div key={i} style={{ padding: '10px 12px', borderRadius: 0, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)' }}>
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
                    background: 'rgba(245,158,11,0.08)',
                    border: '1px solid rgba(245,158,11,0.25)',
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
                    background: verifyResult.ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
                    border: `1px solid ${verifyResult.ok ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.25)'}`,
                  }}>
                    <div className="tiny" style={{
                      fontWeight: 600,
                      color: verifyResult.ok ? 'var(--ok, #22c55e)' : 'var(--err)',
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

// ---- QueueScreen ----
type FilterKey = 'all' | 'active' | 'queued' | 'done' | 'failed';

export function QueueScreen() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [laminusDown, setLaminusDown] = useState(false);

  const { jobs: rawJobs, refetch } = useQueue();
  const [printers] = useFleetData();

  React.useEffect(() => {
    let alive = true;
    function poll() {
      fetch('/api/v1/laminus/catalog/status')
        .then(r => r.ok ? r.json() : Promise.reject())
        .then((d: { laminus: unknown }) => { if (alive) setLaminusDown(d.laminus === null); })
        .catch(() => { if (alive) setLaminusDown(true); });
    }
    poll();
    const id = setInterval(poll, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Collect unique file IDs to load plate metadata
  const fileIds = useMemo(() => [...new Set(rawJobs.map(j => j.uploaded_file_id))], [rawJobs]);
  const { getPlate, getFileName } = useFilePlates(fileIds);

  // Map ApiJob → DisplayJob
  const jobs: DisplayJob[] = useMemo(() => {
    return rawJobs.map(j => {
      const plate = getPlate(j.uploaded_file_id, j.plate_number);
      const fileName = getFileName(j.uploaded_file_id);

      // Prefer job-level estimate (from test-slice) over plate metadata
      const estTime = Math.round(
        ((j.estimate_status === 'done' && j.estimate_seconds != null
          ? j.estimate_seconds
          : plate?.estimated_time) ?? 0) / 60
      );
      const filamentG =
        j.estimate_status === 'done' && j.estimate_filament_grams != null
          ? j.estimate_filament_grams
          : (plate?.filament_g ?? 0);

      let elapsed = 0;
      let progress = 0;
      let layer: { now: number; total: number } | null = null;
      let printerName: string | null = null;
      if (j.status === 'printing' || j.status === 'paused') {
        const printer = printers.find(p => p.id === String(j.assigned_printer_id));
        if (printer) {
          progress = printer.progress ?? 0;
          layer = printer.layer ?? null;
          elapsed = estTime - (printer.timeRemaining ?? 0);
          printerName = printer.name ?? null;
        }
      }

      // sliced = gcode ready; statuses where slicing hasn't happened or failed
      const sliced = ['sliced', 'uploading', 'printing', 'paused'].includes(j.status);

      return {
        id: String(j.id),
        rawId: j.id,
        fileName: fileName ?? null,
        plateName: `Plate ${j.plate_number}`,
        status: j.status,
        blockReason: j.block_reason ?? null,
        materials: j.materials ?? [],
        eligiblePrinters: j.eligible_printers ?? [],
        estTime,
        filamentG,
        elapsed,
        progress,
        layer,
        sliced,
        queuePosition: j.queue_position ?? 0,
        ordinalRank: 0,   // filled in after sort below
        fileId: j.uploaded_file_id,
        thumbnailPath: plate?.thumbnail_path ?? null,
        printerName,
      };
    }).sort((a, b) => {
      const order: Record<string, number> = { printing: 0, paused: 0, slicing: 1, uploading: 1, queued: 2, blocked: 2, complete: 3, failed: 4 };
      const sa = order[a.status] ?? 9;
      const sb = order[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      return a.queuePosition - b.queuePosition;
    }).map((job, _, arr) => {
      const queueable = arr.filter(j => j.status === 'queued' || j.status === 'blocked');
      const rank = queueable.findIndex(j => j.id === job.id);
      return { ...job, ordinalRank: rank >= 0 ? rank + 1 : 0 };
    });
  }, [rawJobs, getPlate, getFileName, printers]);

  const filtered = jobs.filter(j => {
    if (filter === 'all') return true;
    if (filter === 'active') return j.status === 'printing' || j.status === 'paused' || j.status === 'slicing' || j.status === 'uploading';
    if (filter === 'queued') return j.status === 'queued' || j.status === 'blocked';
    if (filter === 'done') return j.status === 'complete';
    if (filter === 'failed') return j.status === 'failed';
    return true;
  });

  const totals = {
    active: jobs.filter(j => ['printing', 'paused', 'slicing', 'uploading'].includes(j.status)).length,
    queued: jobs.filter(j => j.status === 'queued' || j.status === 'blocked').length,
    done: jobs.filter(j => j.status === 'complete').length,
    failed: jobs.filter(j => j.status === 'failed').length,
    timeLeft: jobs.filter(j => j.status === 'queued' || j.status === 'blocked').reduce((acc, j) => acc + j.estTime, 0),
  };

  const selectedJob = selectedJobId != null ? jobs.find(j => j.rawId === selectedJobId) ?? null : null;

  const showStats = filter === 'all';

  const showStatusPill = (job: DisplayJob) => {
    if (filter === 'all') return job.status === 'printing' || job.status === 'paused';
    return true;
  };

  async function handleCancel(jobId: number) {
    try {
      await cancelJob(jobId);
      if (selectedJobId === jobId) setSelectedJobId(null);
      refetch();
    } catch (err) {
      console.error('Failed to cancel job:', err);
    }
  }

  async function handleUnblock(jobId: number) {
    try {
      await unblockJob(jobId);
      refetch();
    } catch (err) {
      console.error('Failed to unblock job:', err);
    }
  }

  async function handleReorder(jobId: number, action: 'promote' | 'demote' | 'front' | 'back') {
    try {
      await reorderJob(jobId, action);
      refetch();
    } catch (err) {
      console.error('Failed to reorder job:', err);
    }
  }

  return (
    <div
      className="screen-grid"
      style={{ gridTemplateColumns: selectedJob ? '1fr 360px' : '1fr', gap: 18 }}
    >
      <div>
        {/* Summary strip — only shown for "all" filter */}
        {showStats && (
          <div className="row gap-3" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
            <SummaryStat
              label="In progress"
              value={totals.active}
              sub="printing or slicing"
            />
            <SummaryStat
              label="In queue"
              value={totals.queued}
              sub="ready when free"
            />
            <SummaryStat
              label="Queue time"
              value={totals.timeLeft > 0 ? fmtTime(totals.timeLeft) : '—'}
              sub="serial est."
              mono
            />
          </div>
        )}

        {/* Laminus health banner */}
        {laminusDown && (
          <div style={{
            padding: '10px 14px',
            marginBottom: 14,
            borderRadius: 6,
            background: 'rgba(245,158,11,0.1)',
            border: '1px solid rgba(245,158,11,0.35)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            <span style={{ color: 'var(--warn, #f59e0b)', fontSize: 15 }}>⚠</span>
            <span className="small" style={{ color: 'var(--text-1)' }}>
              <strong>Laminus sidecar is unreachable.</strong> Slicing is paused — queued jobs will be blocked until the sidecar comes back online.
            </span>
          </div>
        )}

        {/* Filter + actions */}
        <div className="row between" style={{ marginBottom: 14 }}>
          <div className="row gap-2">
            <FilterChip active={filter === 'all'} onClick={() => setFilter('all')}>
              All{' '}
              <span className="num muted" style={{ marginLeft: 4 }}>
                {jobs.length}
              </span>
            </FilterChip>
            {filter !== 'queued' && filter !== 'done' && (
              <FilterChip
                active={filter === 'active'}
                onClick={() => setFilter('active')}
                ariaLabel="Active"
              >
                Active
              </FilterChip>
            )}
            <FilterChip
              active={filter === 'queued'}
              onClick={() => setFilter('queued')}
              ariaLabel="Queued"
            >
              Queued
            </FilterChip>
            <FilterChip active={filter === 'done'} onClick={() => setFilter('done')}>
              Done{' '}
              <span className="num muted" style={{ marginLeft: 4 }}>
                {totals.done}
              </span>
            </FilterChip>
            {totals.failed > 0 && (
              <FilterChip active={filter === 'failed'} onClick={() => setFilter('failed')}>
                Failed <span className="num muted" style={{ marginLeft: 4 }}>{totals.failed}</span>
              </FilterChip>
            )}
          </div>
          <div className="row gap-2">
            <button className="btn sm">
              <span style={{ display: 'inline-flex' }}>{Icons.sort}</span> Sort
            </button>
            <button className="btn sm">
              <span style={{ display: 'inline-flex' }}>{Icons.filter}</span> Material
            </button>
            <button className="btn primary sm" onClick={() => navigate('/queue/new')}>
              {Icons.plus} New job
            </button>
          </div>
        </div>

        {/* Job list */}
        <div className="col" style={{ gap: 8 }}>
          {filtered.map((job, i) => (
            <JobCardRich
              key={job.id}
              job={job}
              position={i + 1}
              selected={selectedJobId === job.rawId}
              showStatus={showStatusPill(job)}
              onClick={() => setSelectedJobId(selectedJobId === job.rawId ? null : job.rawId)}
            />
          ))}
          {filtered.length === 0 && (
            <Empty title="Nothing here" sub="Try a different filter or add a new job." icon={Icons.queue} />
          )}
        </div>
      </div>

      {selectedJob && (
        <JobDetailPanel
          job={selectedJob}
          onClose={() => setSelectedJobId(null)}
          onCancel={handleCancel}
          onUnblock={handleUnblock}
          onReorder={handleReorder}
        />
      )}
    </div>
  );
}
