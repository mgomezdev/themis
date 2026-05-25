import React, { useState } from 'react';
import { PRINTERS, JOBS } from '../data/mock';
import { fmtTime } from '../data/helpers';
import {
  StatusPill,
  Progress,
  VideoTile,
  Swatch,
  MaterialChip,
  Kv,
} from '../components/ui';
import { Icons } from '../components/icons';
import type { Printer, Job } from '../data/types';

type Layout = 'grid' | 'list' | 'focus';

// -------------------------------------------------------------------------
// Helper: resolve the parts list for a job (joined with order part names)
// -------------------------------------------------------------------------
function partsFromJob(job: Job) {
  return job.parts.map(p => ({
    name: p.partId,
    orderId: p.orderId,
    material: job.material,
    qty: p.qty,
  }));
}

// -------------------------------------------------------------------------
// Telem row — temperature readout
// -------------------------------------------------------------------------
function Telem({
  label,
  value,
  target,
  tone,
}: {
  label: string;
  value: string;
  target: string;
  tone?: string | null;
}) {
  return (
    <div className="row between">
      <div className="small muted">{label}</div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span
          className="num"
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: tone === 'warn' ? 'var(--warn)' : 'var(--text-1)',
          }}
        >
          {value}
        </span>
        <span className="num tiny muted">/ {target}</span>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// PrinterTile — compact card for grid layout
// -------------------------------------------------------------------------
function PrinterTile({
  printer: p,
  onClick,
}: {
  printer: Printer;
  onClick: () => void;
}) {
  const isPrinting = p.status === 'printing';
  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        cursor: 'pointer',
        padding: 0,
        overflow: 'hidden',
        transition: 'border-color 120ms ease',
      }}
    >
      {/* Header strip */}
      <div className="row between" style={{ padding: '12px 14px 8px' }}>
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
        <StatusPill status={p.status} />
      </div>

      <div style={{ padding: '0 14px' }}>
        <VideoTile live={isPrinting} />
      </div>

      <div className="row between" style={{ padding: '12px 14px 10px' }}>
        <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
          <Swatch color={p.material.color} />
          <div className="col" style={{ minWidth: 0 }}>
            <div
              className="small"
              style={{
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {p.material.name}
            </div>
            <div className="tiny muted">{p.material.type}</div>
          </div>
        </div>
        {isPrinting ? (
          <div
            className="col"
            style={{ alignItems: 'flex-end', whiteSpace: 'nowrap' }}
          >
            <div
              className="num"
              style={{
                fontSize: 16,
                fontWeight: 600,
                letterSpacing: '-0.01em',
                color: 'var(--text-1)',
              }}
            >
              {fmtTime(p.timeRemaining)}
            </div>
            <div className="tiny muted">remaining</div>
          </div>
        ) : (
          <div
            className="tiny muted"
            style={{ textAlign: 'right', whiteSpace: 'nowrap' }}
          >
            {p.note ?? '—'}
          </div>
        )}
      </div>

      {isPrinting && (
        <div style={{ padding: '0 14px 14px' }}>
          <Progress value={p.progress} />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">{p.currentJobId}</span>
            <span className="num tiny muted">{p.progress}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// PrinterExpandedCard — full-detail inline card
// -------------------------------------------------------------------------
function PrinterExpandedCard({
  printer: p,
  onCollapse,
}: {
  printer: Printer;
  onCollapse: () => void;
}) {
  const isPrinting = p.status === 'printing';
  const job: Job | undefined = p.currentJobId
    ? JOBS.find(j => j.id === p.currentJobId)
    : undefined;

  return (
    <div
      className="card"
      style={{
        padding: 0,
        overflow: 'hidden',
        borderColor: 'var(--border-3)',
        boxShadow:
          '0 0 0 1px var(--accent-glow), 0 18px 40px -20px rgba(0,0,0,0.6)',
      }}
    >
      {/* Header bar */}
      <div
        className="row between"
        style={{
          padding: '14px 18px',
          background: 'var(--bg-3)',
          borderBottom: '1px solid var(--border-1)',
          gap: 16,
          alignItems: 'center',
        }}
      >
        <div className="col" style={{ minWidth: 0, flex: 1 }}>
          <div
            className="row gap-2"
            style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}
          >
            <div
              style={{
                fontSize: 20,
                fontWeight: 600,
                letterSpacing: '-0.02em',
              }}
            >
              {p.nickname}
            </div>
            <div
              className="muted small"
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                minWidth: 0,
              }}
            >
              {p.name}
            </div>
            <span
              className="mono tiny muted"
              style={{ flexShrink: 0 }}
            >
              · {p.id}
            </span>
          </div>
          <div
            className="tiny muted"
            style={{
              marginTop: 2,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            <span className="num">{p.buildVolume}</span> mm ·{' '}
            {p.chamber ? 'enclosed' : 'open frame'} · capable:{' '}
            {p.capabilities.join(' · ')}
          </div>
        </div>
        <div className="row gap-2" style={{ flexShrink: 0 }}>
          <StatusPill status={p.status} />
          <button className="btn sm">{Icons.camera} Snapshot</button>
          <button className="btn icon sm">{Icons.more}</button>
          <button
            className="btn ghost icon sm"
            title="Collapse"
            onClick={onCollapse}
          >
            {Icons.x}
          </button>
        </div>
      </div>

      {/* Body */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.6fr) minmax(280px, 1fr)',
          gap: 18,
          padding: 18,
        }}
      >
        {/* LEFT */}
        <div className="col gap-4" style={{ minWidth: 0 }}>
          <VideoTile live={isPrinting} time={p.timeElapsed} />
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: -8 }}>
            Live camera — stub (see GitHub issues for wiring)
          </div>

          {/* Big telemetry numbers */}
          <div className="row gap-6" style={{ flexWrap: 'wrap' }}>
            <Kv
              k="Progress"
              v={
                <span
                  className="num"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {p.progress}%
                </span>
              }
            />
            <Kv
              k="Time left"
              v={
                <span
                  className="num"
                  style={{
                    fontSize: 22,
                    fontWeight: 600,
                    color: isPrinting
                      ? 'var(--accent-hi)'
                      : 'var(--text-3)',
                  }}
                >
                  {isPrinting ? fmtTime(p.timeRemaining) : '—'}
                </span>
              }
            />
            <Kv
              k="Elapsed"
              v={
                <span className="num" style={{ fontSize: 22 }}>
                  {isPrinting ? fmtTime(p.timeElapsed) : '—'}
                </span>
              }
            />
            {p.layer && (
              <Kv
                k="Layer"
                v={
                  <span className="num" style={{ fontSize: 22 }}>
                    {p.layer.now}
                    <span
                      className="muted"
                      style={{ fontSize: 14 }}
                    >
                      {' '}
                      / {p.layer.total}
                    </span>
                  </span>
                }
              />
            )}
          </div>
          {isPrinting && <Progress value={p.progress} large />}

          {/* Action row */}
          <div className="row gap-2" style={{ marginTop: 2 }}>
            {isPrinting && (
              <>
                <button className="btn">{Icons.pause} Pause</button>
                <button className="btn">{Icons.stop} Stop</button>
              </>
            )}
            {!isPrinting && (
              <button className="btn primary">
                {Icons.play} Claim next from queue
              </button>
            )}
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm">
              {Icons.external} Open in slicer
            </button>
          </div>

          {/* Current job */}
          {job && (
            <div
              className="card"
              style={{ padding: 14, background: 'var(--bg-1)' }}
            >
              <div
                className="row between"
                style={{ marginBottom: 10, alignItems: 'center' }}
              >
                <div className="col">
                  <span className="tag-key">Current job</span>
                  <div className="row gap-2" style={{ marginTop: 2 }}>
                    <span className="mono tiny muted">{job.id}</span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>
                      {job.plateName}
                    </span>
                  </div>
                </div>
                <button className="btn ghost sm">
                  {Icons.arrowR} Open job
                </button>
              </div>
              <div className="col gap-2">
                {partsFromJob(job).map((part, i) => (
                  <div
                    key={i}
                    className="row between"
                    style={{
                      padding: '8px 12px',
                      background: 'var(--bg-2)',
                      borderRadius: 8,
                      border: '1px solid var(--border-1)',
                    }}
                  >
                    <div className="col" style={{ minWidth: 0 }}>
                      <div
                        className="small"
                        style={{ fontWeight: 500 }}
                      >
                        {part.name}
                      </div>
                      <div className="tiny muted">
                        {part.orderId} · {part.material}
                      </div>
                    </div>
                    <div
                      className="num small"
                      style={{ flexShrink: 0 }}
                    >
                      ×{part.qty}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT */}
        <div className="col gap-4">
          <div
            className="card"
            style={{ padding: 14, background: 'var(--bg-1)' }}
          >
            <div className="tag-key">Loaded material</div>
            <div className="row gap-3" style={{ marginTop: 10 }}>
              <Swatch color={p.material.color} large />
              <div className="col" style={{ minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 500,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {p.material.name}
                </div>
                <div className="tiny muted">{p.material.type}</div>
              </div>
            </div>
            <button
              className="btn sm"
              style={{ marginTop: 12, width: '100%' }}
            >
              {Icons.external} Open in Spool Buddy
            </button>
          </div>

          <div
            className="card"
            style={{ padding: 14, background: 'var(--bg-1)' }}
          >
            <div className="tag-key" style={{ marginBottom: 10 }}>
              Telemetry
            </div>
            <div className="col gap-3">
              <Telem
                label="Nozzle"
                value={`${p.nozzleTemp}°C`}
                target={isPrinting ? '220°C' : '—'}
                tone={isPrinting ? 'warn' : null}
              />
              <Telem
                label="Bed"
                value={`${p.bedTemp}°C`}
                target={isPrinting ? '60°C' : '—'}
              />
              {p.chamberTemp != null && (
                <Telem
                  label="Chamber"
                  value={`${p.chamberTemp}°C`}
                  target={p.chamber ? '60°C' : '—'}
                />
              )}
            </div>
          </div>

          <div
            className="card"
            style={{ padding: 14, background: 'var(--bg-1)' }}
          >
            <div
              className="row between"
              style={{ marginBottom: 10 }}
            >
              <span className="tag-key">Queue eligibility</span>
              <button className="btn ghost sm">Edit</button>
            </div>
            <div className="col gap-2 small">
              {p.capabilities.map(c => (
                <div key={c} className="row gap-2">
                  <span style={{ color: 'var(--ok)' }}>{Icons.check}</span>
                  <span>{c}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FleetGrid layout
// -------------------------------------------------------------------------
function FleetGrid({
  expandedId,
  onToggle,
}: {
  expandedId: string | null;
  onToggle: (id: string) => void;
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        gap: 16,
      }}
    >
      {PRINTERS.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div
            key={p.id}
            style={{ gridColumn: expanded ? '1 / -1' : 'auto' }}
          >
            {expanded ? (
              <PrinterExpandedCard
                printer={p}
                onCollapse={() => onToggle(p.id)}
              />
            ) : (
              <PrinterTile printer={p} onClick={() => onToggle(p.id)} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------------
// FleetList layout — table with inline expand row
// -------------------------------------------------------------------------
function FleetList({
  expandedId,
  onToggle,
}: {
  expandedId: string | null;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 24 }}></th>
            <th style={{ width: 48 }}></th>
            <th>Printer</th>
            <th>Status</th>
            <th>Material</th>
            <th>Current job</th>
            <th style={{ width: 220 }}>Progress</th>
            <th style={{ width: 100, textAlign: 'right' }}>Time left</th>
          </tr>
        </thead>
        <tbody>
          {PRINTERS.map(p => {
            const expanded = expandedId === p.id;
            return (
              <React.Fragment key={p.id}>
                <tr
                  onClick={() => onToggle(p.id)}
                  style={{
                    background: expanded ? 'var(--bg-3)' : undefined,
                  }}
                >
                  <td>
                    <span
                      style={{
                        display: 'inline-flex',
                        color: 'var(--text-3)',
                        transform: expanded ? 'rotate(90deg)' : 'none',
                        transition: 'transform 120ms ease',
                      }}
                    >
                      {Icons.chevR}
                    </span>
                  </td>
                  <td>
                    <div
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: 6,
                        background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
                        border: '1px solid var(--border-1)',
                        display: 'grid',
                        placeItems: 'center',
                      }}
                    >
                      <span
                        className="mono tiny"
                        style={{ color: p.accent }}
                      >
                        {p.badge}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{p.nickname}</div>
                    <div className="tiny muted">{p.name}</div>
                  </td>
                  <td>
                    <StatusPill status={p.status} />
                  </td>
                  <td>
                    <MaterialChip
                      material={p.material.name}
                      color={p.material.color}
                    />
                  </td>
                  <td>
                    {p.currentJobId ? (
                      <span className="mono tiny">{p.currentJobId}</span>
                    ) : (
                      <span className="muted tiny">—</span>
                    )}
                  </td>
                  <td>
                    {p.status === 'printing' ? (
                      <Progress value={p.progress} />
                    ) : (
                      <span className="tiny muted">{p.note ?? '—'}</span>
                    )}
                  </td>
                  <td
                    className="num"
                    style={{
                      textAlign: 'right',
                      color:
                        p.status === 'printing'
                          ? 'var(--text-1)'
                          : 'var(--text-3)',
                    }}
                  >
                    {p.status === 'printing'
                      ? fmtTime(p.timeRemaining)
                      : '—'}
                  </td>
                </tr>
                {expanded && (
                  <tr>
                    <td
                      colSpan={8}
                      style={{
                        padding: 0,
                        background: 'var(--bg-1)',
                        cursor: 'default',
                      }}
                      onClick={e => e.stopPropagation()}
                    >
                      <div style={{ padding: 18 }}>
                        <PrinterExpandedCard
                          printer={p}
                          onCollapse={() => onToggle(p.id)}
                        />
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// -------------------------------------------------------------------------
// PrinterStripCard — thumbnail in focus sidebar rail
// -------------------------------------------------------------------------
function PrinterStripCard({
  printer: p,
  active,
  onClick,
}: {
  printer: Printer;
  active: boolean;
  onClick: () => void;
}) {
  const isPrinting = p.status === 'printing';
  return (
    <div
      onClick={onClick}
      className="card"
      style={{
        padding: 10,
        cursor: 'pointer',
        borderColor: active ? 'var(--accent)' : undefined,
        background: active ? 'var(--bg-3)' : 'var(--bg-2)',
      }}
    >
      <div className="row gap-3">
        <div
          style={{
            width: 72,
            height: 48,
            borderRadius: 6,
            overflow: 'hidden',
            flexShrink: 0,
          }}
        >
          <VideoTile live={isPrinting} />
        </div>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div
            className="row between"
            style={{ alignItems: 'center' }}
          >
            <div className="small" style={{ fontWeight: 500 }}>
              {p.nickname}
            </div>
            <StatusPill status={p.status} />
          </div>
          <div className="row gap-2" style={{ marginTop: 4 }}>
            <Swatch color={p.material.color} />
            <div
              className="tiny muted"
              style={{
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {p.material.name}
            </div>
          </div>
          {isPrinting && (
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="num tiny muted">{p.progress}%</span>
              <span
                className="num tiny"
                style={{ color: 'var(--accent-hi)' }}
              >
                {fmtTime(p.timeRemaining)}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FleetFocus layout — one big hero + sidebar strip
// -------------------------------------------------------------------------
function FleetFocus({
  expandedId: _expandedId,
  onToggle: _onToggle,
}: {
  expandedId: string | null;
  onToggle: (id: string) => void;
}) {
  const [focusId, setFocusId] = useState(PRINTERS[0].id);
  const hero = PRINTERS.find(p => p.id === focusId) ?? PRINTERS[0];
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 320px',
        gap: 18,
      }}
    >
      <PrinterExpandedCard printer={hero} onCollapse={() => {}} />
      <div className="col gap-3">
        {PRINTERS.map(p => (
          <PrinterStripCard
            key={p.id}
            printer={p}
            active={p.id === focusId}
            onClick={() => setFocusId(p.id)}
          />
        ))}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------
// FleetScreen — main export
// -------------------------------------------------------------------------
export function FleetScreen() {
  const [layout, setLayout] = useState<Layout>('grid');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggle = (id: string) =>
    setExpandedId(expandedId === id ? null : id);

  const printingCount = PRINTERS.filter(p => p.status === 'printing').length;
  const idleCount = PRINTERS.filter(p => p.status === 'idle').length;

  return (
    <div className="col gap-5">
      {/* Header */}
      <div className="row between">
        <div>
          <div className="tag-key" style={{ marginBottom: 2 }}>
            Workshop
          </div>
          <div
            className="row gap-3"
            style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}
          >
            <div
              style={{
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: '-0.02em',
              }}
            >
              {PRINTERS.length} printers online
            </div>
            <div className="muted small">
              {printingCount} printing · {idleCount} idle
            </div>
          </div>
        </div>
        <div className="row gap-2">
          {/* Layout toggle buttons */}
          <button
            className={`btn icon sm${layout === 'grid' ? ' active' : ''}`}
            title="Grid layout"
            onClick={() => setLayout('grid')}
          >
            {Icons.fleet}
          </button>
          <button
            className={`btn icon sm${layout === 'list' ? ' active' : ''}`}
            title="List layout"
            onClick={() => setLayout('list')}
          >
            {Icons.layers}
          </button>
          <button
            className={`btn icon sm${layout === 'focus' ? ' active' : ''}`}
            title="Focus layout"
            onClick={() => setLayout('focus')}
          >
            {Icons.panel}
          </button>
          <button
            className="btn sm"
            style={{ whiteSpace: 'nowrap', marginLeft: 8 }}
          >
            {Icons.refresh} Sync now
          </button>
        </div>
      </div>

      {layout === 'list' && (
        <FleetList expandedId={expandedId} onToggle={toggle} />
      )}
      {layout === 'focus' && (
        <FleetFocus expandedId={expandedId} onToggle={toggle} />
      )}
      {layout === 'grid' && (
        <FleetGrid expandedId={expandedId} onToggle={toggle} />
      )}
    </div>
  );
}
