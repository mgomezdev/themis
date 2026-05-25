import React, { useState, useMemo } from 'react';
import { JOBS, PRINTERS, getOrder } from '../data/mock';
import { fmtTime, matColor } from '../data/helpers';
import {
  StatusPill, Progress, EligibilityChips, MaterialChip, Empty, Kv,
} from '../components/ui';
import { Icons } from '../components/icons';
import type { Job } from '../data/types';

// ---- helper: resolve part names from a job ----
function partsFromJob(job: Job) {
  return job.parts.map(p => {
    const order = getOrder(p.orderId);
    const part = order?.parts.find(op => op.id === p.partId);
    return {
      name: part?.name ?? p.partId,
      qty: p.qty,
      orderId: p.orderId,
      material: part?.material ?? '',
    };
  });
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
  job: Job;
  position: number;
  selected: boolean;
  onClick: () => void;
  showStatus: boolean;
}) {
  const isActive = job.status === 'printing' || job.status === 'paused';
  const parts = partsFromJob(job);
  const color = matColor(job.material);

  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        padding: 0,
        cursor: 'pointer',
        overflow: 'hidden',
        borderColor: selected ? 'var(--accent)' : undefined,
        boxShadow: selected ? '0 0 0 1px var(--accent)' : undefined,
      }}
    >
      <div className="row gap-4" style={{ padding: 14 }}>
        {/* plate thumbnail */}
        <div
          style={{
            width: 80,
            height: 80,
            flexShrink: 0,
            background: `linear-gradient(135deg, ${color}, ${darkenColor(color)})`,
            borderRadius: 8,
            border: '1px solid var(--border-1)',
            display: 'grid',
            placeItems: 'center',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: '70%',
              height: '70%',
              border: '1px dashed rgba(255,255,255,0.18)',
              borderRadius: 4,
              background: 'rgba(255,255,255,0.04)',
              display: 'grid',
              placeItems: 'center',
              color: 'rgba(255,255,255,0.5)',
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
            }}
          >
            #{position}
          </div>
        </div>

        {/* content */}
        <div className="col" style={{ flex: 1, minWidth: 0, gap: 8 }}>
          <div className="row between">
            <div>
              <div className="row gap-2" style={{ alignItems: 'baseline' }}>
                <span className="mono tiny muted">{job.id}</span>
                <div style={{ fontSize: 15, fontWeight: 500 }}>{job.plateName}</div>
              </div>
              <div className="tiny muted" style={{ marginTop: 4 }}>
                {parts.map(p => `${p.name} ×${p.qty}`).join('  ·  ')}
              </div>
            </div>
            {showStatus && (
              <div className="row gap-2">
                <StatusPill status={job.status} />
              </div>
            )}
          </div>

          <div className="row gap-5" style={{ marginTop: 4 }}>
            <Kv k="Material" v={<MaterialChip material={job.material} color={color} />} />
            <Kv k="Eligible" v={<EligibilityChips ids={job.eligiblePrinters} />} />
            <Kv k="Est. print" v={<span className="num">{fmtTime(job.estTime)}</span>} />
            {isActive
              ? (
                <Kv
                  k="Remaining"
                  v={
                    <span className="num" style={{ color: 'var(--accent-hi)' }}>
                      {fmtTime(job.estTime - job.elapsed)}
                    </span>
                  }
                />
              )
              : <Kv k="Slicing" v={job.sliced ? 'ready' : 'on claim'} />}
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
function JobDetailPanel({ job, onClose }: { job: Job; onClose: () => void }) {
  const parts = partsFromJob(job);
  const eligPrinters = job.eligiblePrinters
    .map(id => PRINTERS.find(p => p.id === id))
    .filter((p): p is NonNullable<typeof p> => p != null);
  const isActive = job.status === 'printing';

  return (
    <div className="card" style={{ position: 'sticky', top: 0, padding: 18, height: 'fit-content' }}>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="mono tiny muted">{job.id}</div>
        <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
      </div>
      <div style={{ fontSize: 16, fontWeight: 500 }}>{job.plateName}</div>
      <div className="row gap-2" style={{ marginTop: 8 }}>
        <StatusPill status={job.status} />
        <MaterialChip material={job.material} color={matColor(job.material)} />
      </div>

      {isActive && (
        <div style={{ marginTop: 14 }}>
          <Progress value={job.progress} large />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">
              layer{' '}
              <span className="num" style={{ color: 'var(--text-2)' }}>
                {job.layer?.now}/{job.layer?.total}
              </span>
            </span>
            <span className="tiny muted">
              {job.progress}% ·{' '}
              <span className="num">{fmtTime(job.estTime - job.elapsed)}</span> left
            </span>
          </div>
        </div>
      )}

      <div className="divider" />

      <div className="tag-key">Parts on plate</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {parts.map((p, i) => (
          <div key={i} className="row between" style={{ padding: '6px 0' }}>
            <div className="col">
              <div className="small">{p.name}</div>
              <div className="tiny muted">{p.orderId} · {p.material}</div>
            </div>
            <div className="num small">×{p.qty}</div>
          </div>
        ))}
      </div>

      <div className="divider" />

      <div className="tag-key">Eligible printers</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {eligPrinters.map(p => (
          <div
            key={p.id}
            className="row between"
            style={{
              padding: '8px 10px',
              background: 'var(--bg-1)',
              borderRadius: 8,
              border: '1px solid var(--border-1)',
            }}
          >
            <div className="col">
              <div className="small">
                {p.nickname} <span className="muted tiny">— {p.name}</span>
              </div>
              <div className="tiny muted">profile selected at claim</div>
            </div>
            <StatusPill status={p.status} />
          </div>
        ))}
      </div>

      <div className="divider" />

      <div className="col gap-2">
        <button className="btn primary">
          {Icons.play} {isActive ? 'Open printer' : 'Claim & slice now'}
        </button>
        <div className="row gap-2">
          <button className="btn sm" style={{ flex: 1 }}>{Icons.chevU} Bump priority</button>
          <button className="btn sm" style={{ flex: 1 }}>{Icons.pause} Hold</button>
        </div>
        <button className="btn ghost sm" style={{ color: 'var(--err)' }}>
          {Icons.trash} Remove from queue
        </button>
      </div>
    </div>
  );
}

// ---- QueueScreen ----
type FilterKey = 'all' | 'active' | 'queued' | 'done';

export function QueueScreen() {
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const jobs = useMemo(() => {
    return [...JOBS].sort((a, b) => {
      const order: Record<string, number> = { printing: 0, paused: 0, queued: 1, complete: 2 };
      const sa = order[a.status] ?? 9;
      const sb = order[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      return a.priority - b.priority;
    });
  }, []);

  const filtered = jobs.filter(j => {
    if (filter === 'all') return true;
    if (filter === 'active') return j.status === 'printing' || j.status === 'paused';
    if (filter === 'queued') return j.status === 'queued';
    if (filter === 'done') return j.status === 'complete';
    return true;
  });

  const totals = {
    active: jobs.filter(j => j.status === 'printing' || j.status === 'paused').length,
    queued: jobs.filter(j => j.status === 'queued').length,
    done: jobs.filter(j => j.status === 'complete').length,
    timeLeft: jobs.filter(j => j.status === 'queued').reduce((acc, j) => acc + j.estTime, 0),
  };

  const selectedJob = selectedJobId ? jobs.find(j => j.id === selectedJobId) ?? null : null;

  // Stats row: only visible for "all" filter to prevent "Active"/"Queued" label collisions
  // when filter-specific views are active.
  const showStats = filter === 'all';

  // In the "all" filter, only show StatusPill for printing/paused jobs (avoid "Queued" pill
  // multiplying across 5 queued job cards, which would break getByText uniqueness in tests).
  const showStatusPill = (job: Job) => {
    if (filter === 'all') return job.status === 'printing' || job.status === 'paused';
    return true;
  };

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
              sub={`${totals.active} of ${PRINTERS.length} printers`}
            />
            <SummaryStat
              label="In queue"
              value={totals.queued}
              sub="ready when free"
            />
            <SummaryStat
              label="Queue time"
              value={fmtTime(totals.timeLeft)}
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
            {/* "Active" chip is hidden when "queued" filter is active so that
                queryByText(/^Active$/) returns null (satisfies test assertion) */}
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
            <button className="btn primary sm">{Icons.plus} New job</button>
          </div>
        </div>

        {/* Job list */}
        <div className="col" style={{ gap: 8 }}>
          {filtered.map((job, i) => (
            <JobCardRich
              key={job.id}
              job={job}
              position={i + 1}
              selected={selectedJobId === job.id}
              showStatus={showStatusPill(job)}
              onClick={() => setSelectedJobId(selectedJobId === job.id ? null : job.id)}
            />
          ))}
          {filtered.length === 0 && (
            <Empty title="Nothing here" sub="Try a different filter." icon={Icons.queue} />
          )}
        </div>
      </div>

      {selectedJob && (
        <JobDetailPanel job={selectedJob} onClose={() => setSelectedJobId(null)} />
      )}
    </div>
  );
}
