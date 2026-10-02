import type { TempSample } from '../lib/tempHistory';

const SERIES: Array<{ key: 'nozzle' | 'bed' | 'chamber'; label: string; color: string }> = [
  { key: 'nozzle', label: 'Nozzle', color: 'var(--err, #f87171)' },
  { key: 'bed', label: 'Bed', color: 'var(--warn, #f59e0b)' },
  { key: 'chamber', label: 'Chamber', color: 'var(--accent-hi, #22d3ee)' },
];

const W = 600, H = 160, PAD = 28;

/** Dependency-free SVG line chart of the last ~30 min of nozzle/bed/chamber temperatures. */
export function TempChart({ history, windowMs }: { history: TempSample[]; windowMs: number }) {
  const shown = SERIES.filter(s => history.some(h => h[s.key] != null));
  if (history.length < 2 || shown.length === 0) {
    return <div className="small muted" data-testid="temp-chart-empty">Collecting temperature data…</div>;
  }
  const end = history[history.length - 1].t;
  const start = end - windowMs;
  const max = Math.max(50, ...history.flatMap(h => shown.map(s => h[s.key] ?? 0)));
  const x = (t: number) => PAD + ((t - start) / windowMs) * (W - PAD - 6);
  const y = (v: number) => H - PAD / 2 - (v / max) * (H - PAD);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Temperature history" style={{ width: '100%', height: 'auto' }}>
        {[0, 0.5, 1].map(f => (
          <g key={f}>
            <line x1={PAD} x2={W - 6} y1={y(max * f)} y2={y(max * f)} stroke="var(--border-1, #333)" strokeWidth="1" />
            <text x={PAD - 4} y={y(max * f) + 3} textAnchor="end" fontSize="9" fill="var(--text-3, #888)">{Math.round(max * f)}</text>
          </g>
        ))}
        {shown.map(s => {
          const pts = history.filter(h => h[s.key] != null).map(h => `${x(h.t).toFixed(1)},${y(h[s.key] as number).toFixed(1)}`);
          return <polyline key={s.key} data-series={s.key} points={pts.join(' ')} fill="none" stroke={s.color} strokeWidth="1.6" />;
        })}
      </svg>
      <div className="row gap-3 tiny muted" style={{ marginTop: 4 }}>
        {shown.map(s => (
          <span key={s.key}><span style={{ color: s.color }}>●</span> {s.label} {history[history.length - 1][s.key]?.toFixed(0)}°C</span>
        ))}
        <span style={{ marginLeft: 'auto' }}>last {Math.round(windowMs / 60000)} min</span>
      </div>
    </div>
  );
}
