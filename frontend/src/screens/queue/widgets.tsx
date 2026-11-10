// Queue: filter chips, summary stats and the starts-in countdown.
import React from 'react';
import { startsIn } from '../../lib/schedule';

/** Live "in 2h 15m" label for a scheduled job; ticks every 30s and disappears once the job is due. */
export function StartsIn({ iso }: { iso: string }) {
  const [, setTick] = React.useState(0);
  React.useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  const label = startsIn(iso);
  return (
    <span className="num" style={{ color: 'var(--accent-hi)' }} title={`Scheduled for ${new Date(iso).toLocaleString()}`}>
      {label ?? 'due'}
    </span>
  );
}

// ---- FilterChip ----
export function FilterChip({
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
export function SummaryStat({
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
