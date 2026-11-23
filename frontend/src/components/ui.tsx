import React from 'react';
import { Icons } from './icons';
import type { StatusKey } from '../data/types';
import { fmtClock } from '../data/helpers';
import { withKeyParam } from '../api/client';

const STATUS_MAP: Record<string, [string, string]> = {
  printing:    ['info',   'Printing'],
  queued:      ['idle',   'Queued'],
  waiting:     ['idle',   'Waiting'],
  claiming:    ['info',   'Claiming'],
  slicing:     ['info',   'Slicing…'],
  uploading:   ['info',   'Uploading'],
  paused:      ['warn',   'Paused'],
  error:       ['err',    'Error'],
  offline:     ['idle',   'Offline'],
  idle:        ['ok',     'Idle'],
  ready:       ['ok',     'Ready'],
  complete:    ['ok',     'Complete'],
  hold:        ['warn',   'On hold'],
  in_progress: ['info',   'In progress'],
  partial:     ['warn',   'Partial'],
  blocked:     ['warn',   'Blocked'],
  failed:      ['err',    'Failed'],
};

export function StatusPill({ status, label }: { status: StatusKey; label?: string }) {
  const [cls, txt] = STATUS_MAP[status] ?? ['idle', status];
  return <span className={`pill ${cls}`}><span className="dot" />{label ?? txt}</span>;
}

export function Progress({ value = 0, tone, large }: { value?: number; tone?: string; large?: boolean }) {
  const cls = tone === 'warn' ? 'warn' : tone === 'err' ? 'err' : '';
  return (
    <div className={`progress ${cls} ${large ? 'lg' : ''}`}>
      <div className="bar" style={{ '--p': `${Math.max(0, Math.min(100, value))}%` } as React.CSSProperties} />
    </div>
  );
}

export function Swatch({ color, large }: { color: string; large?: boolean }) {
  return <span className={`swatch ${large ? 'lg' : ''}`} style={{ '--c': color } as React.CSSProperties} />;
}

export function MaterialChip({ material, color }: { material: string; color: string }) {
  return (
    <span className="row gap-2" style={{ fontSize: 12 }}>
      <Swatch color={color} />
      <span>{material}</span>
    </span>
  );
}

const SNAPSHOT_INTERVAL_MS = 2000;

const cameraImageStyle: React.CSSProperties = {
  position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', display: 'block',
};

function SnapshotFeed({ printerId, interval }: { printerId: string; interval: number }) {
  const [visible, setVisible] = React.useState<number | null>(null);
  const [pending, setPending] = React.useState<number | null>(0);
  const next = React.useRef(1);

  React.useEffect(() => {
    if (pending !== null) return;
    const timer = setTimeout(() => setPending(next.current++), interval);
    return () => clearTimeout(timer);
  }, [interval, pending]);

  // Promote the same decoded image so no-store snapshots are fetched only once per frame.
  return [visible, pending].map(frame => frame === null ? null : (
    <img
      key={frame}
      src={withKeyParam(`/api/v1/printers/${printerId}/snapshot?t=${frame}`)}
      style={frame === visible ? cameraImageStyle : { ...cameraImageStyle, visibility: 'hidden' }}
      onLoad={frame === pending ? () => {
        setVisible(frame);
        setPending(null);
      } : undefined}
      onError={frame === pending ? () => setPending(null) : undefined}
      alt=""
    />
  ));
}

function CameraTileFeed({ printerId, interval, stream }: { printerId: string; interval: number; stream: boolean }) {
  const [streamFailed, setStreamFailed] = React.useState(false);
  return stream && !streamFailed
    ? <img src={withKeyParam(`/api/v1/printers/${printerId}/camera`)} style={cameraImageStyle} data-live-camera onError={() => setStreamFailed(true)} alt="" />
    : <SnapshotFeed printerId={printerId} interval={interval} />;
}

export function VideoTile({
  live = true,
  status,
  time,
  printerId,
  intervalMs,
  noSnapshotsWhileIdle,
  stream = false,
}: {
  live?: boolean;
  status?: StatusKey;
  time?: number;
  printerId?: string;
  intervalMs?: number;
  noSnapshotsWhileIdle?: boolean;
  stream?: boolean;
}) {
  const paused = noSnapshotsWhileIdle && status !== 'printing';
  return (
    <div className="video">
      <div className="feed-scene" />
      <div className="feed-noise" />
      {live && printerId && !paused && (
        <CameraTileFeed key={printerId} printerId={printerId} interval={intervalMs ?? SNAPSHOT_INTERVAL_MS} stream={stream} />
      )}
      {time != null && <div className="feed-time mono">{fmtClock(time)}</div>}
      {status && (
        <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 3 }}>
          <StatusPill status={status} />
        </div>
      )}
    </div>
  );
}

export function Card({ children, className = '', ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`card ${className}`} {...rest}>{children}</div>;
}

export function SectionHeader({ title, sub, actions }: { title: React.ReactNode; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="row between" style={{ marginBottom: 14 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ title, sub, icon }: { title: string; sub?: string; icon: React.ReactNode }) {
  return (
    <div className="col" style={{ alignItems: 'center', padding: '60px 20px', color: 'var(--text-3)' }}>
      <div style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--bg-2)',
                    display: 'grid', placeItems: 'center', marginBottom: 12, color: 'var(--text-3)' }}>
        {icon}
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500 }}>{title}</div>
      {sub && <div className="small muted" style={{ marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="col">
      <div className="tag-key">{k.toUpperCase()}</div>
      <div style={{ marginTop: 4 }}>{v}</div>
    </div>
  );
}

// Re-export Icons for convenience
export { Icons };
