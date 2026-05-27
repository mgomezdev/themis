import React from 'react';
import { Icons } from './icons';
import { PRINTERS } from '../data/mock';
import type { StatusKey } from '../data/types';
import { fmtClock } from '../data/helpers';

export function StatusPill({ status, label }: { status: StatusKey; label?: string }) {
  const map: Record<string, [string, string]> = {
    printing:    ['info',   'Printing'],
    queued:      ['idle',   'Queued'],
    waiting:     ['idle',   'Waiting'],
    claiming:    ['accent', 'Claiming'],
    slicing:     ['accent', 'Slicing'],
    paused:      ['warn',   'Paused'],
    error:       ['err',    'Error'],
    offline:     ['idle',   'Offline'],
    idle:        ['ok',     'Idle'],
    ready:       ['ok',     'Ready'],
    complete:    ['ok',     'Complete'],
    hold:        ['warn',   'On hold'],
    in_progress: ['info',   'In progress'],
    partial:     ['warn',   'Partial'],
  };
  const [cls, txt] = map[status] ?? ['idle', status];
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

export function VideoTile({
  live = true,
  status,
  time,
  printerId,
}: {
  live?: boolean;
  status?: StatusKey;
  time?: number;
  printerId?: string;
}) {
  const [imgError, setImgError] = React.useState(false);
  const [useSnapshot, setUseSnapshot] = React.useState(false);
  const [snapTick, setSnapTick] = React.useState(0);

  React.useEffect(() => {
    setImgError(false);
    setUseSnapshot(false);
    setSnapTick(0);
  }, [printerId]);

  React.useEffect(() => {
    if (!useSnapshot || !live || !printerId) return;
    const id = setInterval(() => setSnapTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [useSnapshot, live, printerId]);

  const showCamera = live && printerId && !imgError;

  return (
    <div className={`video ${live ? 'live' : ''}`}>
      {showCamera ? (
        useSnapshot ? (
          <img
            key={snapTick}
            src={`/api/v1/printers/${printerId}/snapshot?t=${snapTick}`}
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            onError={() => setImgError(true)}
            alt=""
          />
        ) : (
          <img
            src={`/api/v1/printers/${printerId}/camera`}
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            onError={() => setUseSnapshot(true)}
            alt=""
          />
        )
      ) : (
        <>
          <div className="feed-scene" />
          <div className="feed-noise" />
        </>
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

export function PrinterBadge({ printerId }: { printerId: string }) {
  const p = PRINTERS.find(pr => pr.id === printerId);
  if (!p) return null;
  return <span className="elig on" title={p.name}>{p.badge}</span>;
}

export function EligibilityChips({ ids }: { ids: string[] }) {
  return (
    <span className="row gap-2" style={{ flexWrap: 'wrap' }}>
      {PRINTERS.map(p => (
        <span key={p.id} className={`elig ${ids.includes(p.id) ? 'on' : 'off'}`} title={p.name}>
          {p.badge}
        </span>
      ))}
    </span>
  );
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
      <div style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--bg-3)',
                    display: 'grid', placeItems: 'center', marginBottom: 12, color: 'var(--text-3)' }}>
        {icon}
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-2)', fontWeight: 500 }}>{title}</div>
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
