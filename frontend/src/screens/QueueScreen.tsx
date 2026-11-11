import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fmtTime } from '../data/helpers';
import { Empty } from '../components/ui';
import { Icons } from '../components/icons';
import { useQueue, useFilePlates, cancelJob, unblockJob, reorderJob } from '../api/queue';
import { useFleetData } from '../api/fleet';
import { useProjects } from '../api/projects';
import { QueuePools } from '../components/QueuePools';
import { apiFetch } from '../api/client';
import { DisplayJob } from './queue/model';
import { FilterChip, SummaryStat } from './queue/widgets';
import { JobCardRich } from './queue/JobCardRich';
import { JobDetailPanel } from './queue/JobDetailPanel';

// ---- QueueScreen ----
type FilterKey = 'all' | 'active' | 'queued' | 'done' | 'failed';

export function QueueScreen() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [laminusDown, setLaminusDown] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { jobs: rawJobs, refetch } = useQueue();
  const [printers] = useFleetData();

  React.useEffect(() => {
    let alive = true;
    function poll() {
      apiFetch('/api/v1/laminus/catalog/status')
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

  const { projects, refetch: refetchProjects } = useProjects();
  const projectNames = useMemo(() => new Map(projects.map(p => [p.id, p.name])), [projects]);
  const [poolNames, setPoolNames] = useState<Map<number, string>>(new Map());

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
        projectId: j.project_id ?? null,
        projectName: j.project_id != null ? (projectNames.get(j.project_id) ?? `Project #${j.project_id}`) : null,
        poolName: j.pool_group_id != null ? (poolNames.get(j.pool_group_id) ?? 'group') : null,
        sliceCache: {
          status: j.status, save_slice: j.save_slice ?? false, sliced_version_id: j.sliced_version_id ?? null,
          slice_cache_info: j.slice_cache_info ?? null,
        },
        plateName: `Plate ${j.plate_number}`,
        status: j.status,
        blockReason: j.block_reason ?? null,
        notBefore: j.not_before ?? null,
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
        lowStockWarning: j.low_stock_warning ?? null,
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
  }, [rawJobs, getPlate, getFileName, printers, poolNames]);

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

  // A rejected cancel/unblock/reorder used to be logged and nothing else: the job just stayed put and the
  // operator couldn't tell whether the click had done anything. Say what failed until the next action.
  async function runJobAction(what: string, jobId: number, action: () => Promise<unknown>, onDone?: () => void) {
    setActionError(null);
    try {
      await action();
      onDone?.();
      refetch();
    } catch (err) {
      console.error(`Failed to ${what} job:`, err);
      setActionError(`Failed to ${what} job #${jobId}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  const handleCancel = (jobId: number) =>
    runJobAction('cancel', jobId, () => cancelJob(jobId), () => { if (selectedJobId === jobId) setSelectedJobId(null); });

  const handleUnblock = (jobId: number) => runJobAction('unblock', jobId, () => unblockJob(jobId));

  const handleReorder = (jobId: number, action: 'promote' | 'demote' | 'front' | 'back') =>
    runJobAction('reorder', jobId, () => reorderJob(jobId, action));

  return (
    <div
      className="screen-grid queue-grid"
      data-selected={selectedJob ? 'true' : 'false'}
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

        {actionError && (
          <div role="alert" style={{
            padding: '10px 14px', marginBottom: 14, borderRadius: 6,
            background: 'color-mix(in oklch, var(--err) 12%, transparent)', border: '1px solid color-mix(in oklch, var(--err) 30%, transparent)',
            color: 'var(--err)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span className="grow">{actionError}</span>
            <button className="btn ghost icon sm" aria-label="Dismiss" onClick={() => setActionError(null)}>{Icons.x}</button>
          </div>
        )}

        <QueuePools refreshKey={rawJobs} onPools={ps => setPoolNames(new Map(ps.map(p => [p.group_id, p.name])))} />

        {/* Laminus health banner */}
        {laminusDown && (
          <div style={{
            padding: '10px 14px',
            marginBottom: 14,
            borderRadius: 6,
            background: 'color-mix(in oklch, var(--warn) 10%, transparent)',
            border: '1px solid color-mix(in oklch, var(--warn) 35%, transparent)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            <span style={{ color: 'var(--warn)', fontSize: 15 }}>⚠</span>
            <span className="small" style={{ color: 'var(--text-1)' }}>
              <strong>Laminus sidecar is unreachable.</strong> Slicing is paused — queued jobs will be blocked until the sidecar comes back online.
            </span>
          </div>
        )}

        {/* Filter + actions */}
        <div className="row between wrap" style={{ marginBottom: 14 }}>
          <div className="row gap-2 wrap">
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
          onProjectChanged={() => { refetch(); refetchProjects(); }}
        />
      )}
    </div>
  );
}
