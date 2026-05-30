import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fmtTime, matColor } from '../data/helpers';
import {
  StatusPill, Progress, EligibilityChips, MaterialChip, Empty, Kv,
} from '../components/ui';
import { Icons } from '../components/icons';
import { useQueue, useFilePlates, cancelJob, plateThumbnailUrl, type ApiJob } from '../api/queue';

// ---- DisplayJob: flattened shape for rendering ----
interface DisplayJob {
  id: string;
  rawId: number;
  plateName: string;
  status: string;
  material: string;
  eligiblePrinters: string[];
  estTime: number;
  filamentG: number;
  elapsed: number;
  progress: number;
  layer: { now: number; total: number } | null;
  sliced: boolean;
  queuePosition: number;
  fileId: number;
  thumbnailPath: string | null;
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

// ---- color helper ----
function darkenColor(hex: string): string {
  const n = parseInt(hex.replace('#', ''), 16);
  const r = Math.max(0, ((n >> 16) & 0xff) - 40);
  const g = Math.max(0, ((n >> 8) & 0xff) - 40);
  const b = Math.max(0, (n & 0xff) - 40);
  return '#' + ((r << 16) | (g << 8) | b).toString(16).padStart(6, '0');
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
  const color = matColor(job.material);
  const thumbUrl = plateThumbnailUrl(job.fileId, job.thumbnailPath);

  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        padding: 0,
        cursor: 'pointer',
        overflow: 'hidden',
        borderColor: selected ? 'var(--accent)' : isFailed ? 'rgba(239,68,68,0.3)' : undefined,
        boxShadow: selected ? '0 0 0 1px var(--accent)' : undefined,
      }}
    >
      <div className="row gap-4" style={{ padding: 14 }}>
        {/* plate thumbnail */}
        <div
          style={{
            width: 80, height: 80, flexShrink: 0,
            background: `linear-gradient(135deg, #1e3a6e, #3b82f6)`,
            borderRadius: 8,
            border: '1px solid var(--border-1)',
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
            </div>
            {(showStatus || isFailed) && (
              <div className="row gap-2">
                <StatusPill status={job.status} />
              </div>
            )}
          </div>

          <div className="row gap-5" style={{ marginTop: 4 }}>
            {job.material !== '—' && (
              <Kv k="Material" v={<MaterialChip material={job.material} color={color} />} />
            )}
            {job.eligiblePrinters.length > 0 && (
              <Kv k="Eligible" v={<EligibilityChips ids={job.eligiblePrinters} />} />
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
            ) : isFailed ? (
              <Kv k="Slicing" v={<span style={{ color: 'var(--err)' }}>failed</span>} />
            ) : (
              <Kv k="Slicing" v={job.sliced ? 'ready' : 'on claim'} />
            )}
          </div>

          {isActive && (
            <div style={{ marginTop: 4 }}>
              <Progress value={job.progress} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- JobDetailPanel ----
function JobDetailPanel({
  job,
  onClose,
  onCancel,
}: {
  job: DisplayJob;
  onClose: () => void;
  onCancel: (jobId: number) => void;
}) {
  const isActive = job.status === 'printing' || job.status === 'paused';
  const isFailed = job.status === 'failed';
  const cancellable = ['queued', 'slicing', 'uploading', 'printing', 'paused', 'failed'].includes(job.status);
  const thumbUrl = plateThumbnailUrl(job.fileId, job.thumbnailPath);

  return (
    <div className="card" style={{ position: 'sticky', top: 0, padding: 0, height: 'fit-content', overflow: 'hidden' }}>
      {/* Thumbnail header */}
      <div style={{
        width: '100%', height: 180, position: 'relative',
        background: 'linear-gradient(135deg, #1e3a6e, #3b82f6)',
        display: 'grid', placeItems: 'center',
      }}>
        {thumbUrl ? (
          <img src={thumbUrl} alt={job.plateName}
               style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
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
            padding: '3px 8px', borderRadius: 6,
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
          <StatusPill status={job.status} />
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
          <div className="row between">
            <span className="small muted">Slicing</span>
            <span className="small">{job.sliced ? 'Ready' : 'On claim'}</span>
          </div>
          {job.queuePosition > 0 && (
            <div className="row between">
              <span className="small muted">Queue position</span>
              <span className="num small">#{job.queuePosition}</span>
            </div>
          )}
        </div>

        {job.eligiblePrinters.length > 0 && (
          <>
            <div className="divider" />
            <div className="tag-key" style={{ marginBottom: 8 }}>Eligible printers</div>
            <div className="col gap-2" style={{ marginBottom: 14 }}>
              {job.eligiblePrinters.map(id => (
                <div key={id} className="row between" style={{
                  padding: '6px 10px', background: 'var(--bg-1)',
                  borderRadius: 8, border: '1px solid var(--border-1)',
                }}>
                  <div className="small muted">Printer {id}</div>
                </div>
              ))}
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
type FilterKey = 'all' | 'active' | 'queued' | 'done';

export function QueueScreen() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);

  const { jobs: rawJobs, refetch } = useQueue();

  // Collect unique file IDs to load plate metadata
  const fileIds = useMemo(() => [...new Set(rawJobs.map(j => j.uploaded_file_id))], [rawJobs]);
  const getPlate = useFilePlates(fileIds);

  // Map ApiJob → DisplayJob
  const jobs: DisplayJob[] = useMemo(() => {
    return rawJobs.map(j => {
      const plate = getPlate(j.uploaded_file_id, j.plate_number);
      return {
        id: String(j.id),
        rawId: j.id,
        plateName: plate ? `Plate ${j.plate_number}` : `Plate ${j.plate_number}`,
        status: j.status,
        material: '—',
        eligiblePrinters: [],
        estTime: plate?.estimated_time ?? 0,
        filamentG: plate?.filament_g ?? 0,
        elapsed: 0,
        progress: 0,
        layer: null,
        sliced: j.status !== 'queued',
        queuePosition: j.queue_position ?? 0,
        fileId: j.uploaded_file_id,
        thumbnailPath: plate?.thumbnail_path ?? null,
      };
    }).sort((a, b) => {
      const order: Record<string, number> = { printing: 0, paused: 0, slicing: 1, uploading: 1, queued: 2, complete: 3 };
      const sa = order[a.status] ?? 9;
      const sb = order[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      return a.queuePosition - b.queuePosition;
    });
  }, [rawJobs, getPlate]);

  const filtered = jobs.filter(j => {
    if (filter === 'all') return true;
    if (filter === 'active') return j.status === 'printing' || j.status === 'paused' || j.status === 'slicing' || j.status === 'uploading';
    if (filter === 'queued') return j.status === 'queued';
    if (filter === 'done') return j.status === 'complete';
    return true;
  });

  const totals = {
    active: jobs.filter(j => ['printing', 'paused', 'slicing', 'uploading'].includes(j.status)).length,
    queued: jobs.filter(j => j.status === 'queued').length,
    done: jobs.filter(j => j.status === 'complete').length,
    timeLeft: jobs.filter(j => j.status === 'queued').reduce((acc, j) => acc + j.estTime, 0),
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
        />
      )}
    </div>
  );
}
