// Queue: one job as a card in the list.
import { SliceCacheMarkers } from '../../components/SliceCache';
import { fmtTime, matColor } from '../../data/helpers';
import { StatusPill, Progress, MaterialChip, Kv } from '../../components/ui';
import { StlPreview } from '../../components/StlPreview';
import { Icons } from '../../components/icons';
import { plateThumbnailUrl } from '../../api/queue';
import type { StatusKey } from '../../data/types';
import { startsIn } from '../../lib/schedule';
import { StartsIn } from './widgets';
import { DisplayJob, getErrorCategory, getErrorMessage } from './model';

// ---- JobCardRich ----
export function JobCardRich({
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
        borderColor: selected ? 'var(--accent)' : isFailed ? 'color-mix(in oklch, var(--err) 55%, transparent)' : isBlocked ? 'color-mix(in oklch, var(--warn) 60%, transparent)' : undefined,
        boxShadow: selected ? 'inset 0 0 0 1px var(--accent)' : isFailed ? 'inset 0 0 0 1px color-mix(in oklch, var(--err) 15%, transparent)' : isBlocked ? 'inset 0 0 0 1px color-mix(in oklch, var(--warn) 12%, transparent)' : undefined,
      }}
    >
      <div className="row gap-4" style={{ padding: 14 }}>
        {/* plate thumbnail */}
        <div
          style={{
            width: 80, height: 80, flexShrink: 0,
            background: `linear-gradient(135deg, var(--bg-1), var(--bg-2))`,
            borderRadius: 0,
            border: '1px solid var(--border-2)',
            display: 'grid', placeItems: 'center',
            overflow: 'hidden',
          }}
        >
          {thumbUrl ? (
            <img src={thumbUrl} alt={job.plateName}
                 style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : job.fileName?.toLowerCase().endsWith('.stl') ? (
            <StlPreview fileId={job.fileId} size={80} />
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
              {job.projectName && (
                <div className="tiny" style={{ color: 'var(--accent-hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                     title={`Project: ${job.projectName}`}>
                  {Icons.layers} {job.projectName}{job.poolName && ` · pool ${job.poolName}`}
                </div>
              )}
            </div>
            <div className="row gap-2">
              <SliceCacheMarkers job={job.sliceCache} />
              {(showStatus || isFailed) && <StatusPill status={job.status as StatusKey} />}
            </div>
          </div>

          <div className="row gap-5 wrap" style={{ marginTop: 4, rowGap: 8 }}>
            {job.materials.length > 0 && (
              <Kv k="Material" v={
                <div className="row gap-1">
                  {job.materials.map(m => <MaterialChip key={m} material={m} color={matColor(m)} />)}
                </div>
              } />
            )}
            {job.eligiblePrinters.length > 0 && (
              <Kv k="Eligible" v={
                <span className="row gap-1 wrap">
                  {job.eligiblePrinters.map(p => (
                    <span key={p.id} className="pill idle">{p.name}</span>
                  ))}
                </span>
              } />
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
            {!isActive && !isFailed && (job.status === 'queued' || isBlocked) && startsIn(job.notBefore) && (
              <Kv k="Starts" v={<StartsIn iso={job.notBefore!} />} />
            )}
            {!isActive && !isFailed && (
              <Kv k="Slicing" v={
                isBlocked && job.blockReason?.toLowerCase().includes('slice')
                  ? <span className="text-err">failed</span>
                  : isBlocked
                    ? <span className="text-warn">blocked</span>
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
            borderTop: '1px solid color-mix(in oklch, var(--warn) 30%, transparent)',
            background: 'color-mix(in oklch, var(--warn) 9%, transparent)',
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
            borderTop: '1px solid color-mix(in oklch, var(--err) 30%, transparent)',
            background: 'color-mix(in oklch, var(--err) 9%, transparent)',
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
      {!isBlocked && !isFailed && job.lowStockWarning && (
        <div
          style={{
            padding: '10px 14px',
            borderTop: '1px solid color-mix(in oklch, var(--warn) 30%, transparent)',
            background: 'color-mix(in oklch, var(--warn) 9%, transparent)',
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
            <span>Low filament</span>
          </div>
          <span className="tiny" style={{ color: 'var(--text-1)', lineHeight: 1.4, wordBreak: 'break-word' }}>
            {job.lowStockWarning.message}
          </span>
        </div>
      )}
    </div>
  );
}
