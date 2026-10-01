import { useMemo, useState } from 'react';
import { Card, Empty, Icons, Progress, SectionHeader } from '../components/ui';
import { useFleetAnalytics, type AnalyticsStats } from '../api/analytics';

type Preset = '7' | '30' | '90' | 'custom';
const PRESETS: { id: Preset; label: string }[] = [
  { id: '7', label: '7 days' }, { id: '30', label: '30 days' }, { id: '90', label: '90 days' }, { id: 'custom', label: 'Custom' },
];

/** UTC calendar date as YYYY-MM-DD — the backend buckets jobs by UTC day. */
function utcDay(offsetDays = 0): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export function fmtHours(seconds: number): string {
  const h = seconds / 3600;
  return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`;
}

function fmtGrams(g: number): string {
  return g >= 1000 ? `${(g / 1000).toFixed(2)} kg` : `${Math.round(g)} g`;
}

function fmtRate(r: number | null): string {
  return r === null ? '—' : `${r}%`;
}

function fmtCost(c: number | null): string {
  return c === null ? '—' : `$${c.toFixed(2)}`;
}

function Kpi({ label, value, sub, testId }: { label: string; value: string; sub?: string; testId: string }) {
  return (
    <Card style={{ padding: '14px 16px', minWidth: 150, flex: '1 1 150px' }} data-testid={testId}>
      <div className="muted small">{label}</div>
      <div className="num" style={{ fontSize: 24, fontWeight: 600, marginTop: 4, letterSpacing: '-0.01em' }}>{value}</div>
      {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
    </Card>
  );
}

const th = { padding: '8px 12px', fontWeight: 500 } as const;
const td = { padding: '8px 12px', color: 'var(--text-2)', whiteSpace: 'nowrap' } as const;

function kpiSub(t: AnalyticsStats): string {
  return `${t.completed} done · ${t.failed} failed · ${t.cancelled} cancelled`;
}

export function AnalyticsScreen() {
  const [preset, setPreset] = useState<Preset>('30');
  const [customStart, setCustomStart] = useState(() => utcDay(-29));
  const [customEnd, setCustomEnd] = useState(() => utcDay());

  const { start, end } = useMemo(
    () => preset === 'custom'
      ? { start: customStart, end: customEnd }
      : { start: utcDay(-(Number(preset) - 1)), end: utcDay() },
    [preset, customStart, customEnd],
  );
  const customInvalid = preset === 'custom' && (!customStart || !customEnd || customEnd < customStart);
  const { data, error, loading } = useFleetAnalytics(customInvalid ? null : { start, end });

  const maxGrams = Math.max(1, ...(data?.materials.map(m => m.grams) ?? [0]));
  const t = data?.totals;

  return (
    <div className="col gap-4" style={{ padding: '4px 0' }}>
      <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <div role="group" aria-label="Date range" className="row gap-2">
          {PRESETS.map(p => (
            <button key={p.id} className={`btn sm ${preset === p.id ? 'primary' : ''}`} aria-pressed={preset === p.id}
                    onClick={() => setPreset(p.id)}>{p.label}</button>
          ))}
        </div>
        {preset === 'custom' && (
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <input type="date" aria-label="Start date" value={customStart} max={customEnd || undefined}
                   onChange={e => setCustomStart(e.target.value)} />
            <span className="muted small">to</span>
            <input type="date" aria-label="End date" value={customEnd} min={customStart || undefined}
                   onChange={e => setCustomEnd(e.target.value)} />
          </div>
        )}
        {data && !customInvalid && (
          <span className="muted small" data-testid="analytics-range">
            {data.range.start} → {data.range.end} (UTC, {data.range.days} days)
          </span>
        )}
      </div>

      {customInvalid && <div style={{ color: 'var(--err)', fontSize: 13 }}>End date must not be before the start date.</div>}
      {!customInvalid && error && <div role="alert" style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
      {!customInvalid && loading && !data && <div className="muted small">Loading…</div>}

      {!customInvalid && data && t && (
        <>
          <div className="row gap-3" style={{ flexWrap: 'wrap' }}>
            <Kpi testId="kpi-jobs" label="Jobs completed" value={String(t.completed)} sub={kpiSub(t)} />
            <Kpi testId="kpi-success" label="Success rate" value={fmtRate(t.success_rate)} sub="completed ÷ (completed + failed)" />
            <Kpi testId="kpi-hours" label="Print hours" value={fmtHours(t.print_seconds)} sub="completed jobs, slicer estimate" />
            <Kpi testId="kpi-grams" label="Filament used" value={fmtGrams(t.filament_grams)} />
            <Kpi testId="kpi-cost" label="Filament cost" value={fmtCost(t.filament_cost)} sub="recorded costs only" />
          </div>

          <Card style={{ padding: 20 }}>
            <SectionHeader title="Printers" sub="Utilization = print time ÷ range length" />
            {data.printers.length === 0 ? (
              <Empty icon={Icons.printer} title="No printers yet." />
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', color: 'var(--text-3)' }}>
                      <th style={th}>Printer</th><th style={th}>Completed</th><th style={th}>Failed</th>
                      <th style={th}>Success</th><th style={th}>Print time</th><th style={{ ...th, minWidth: 140 }}>Utilization</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.printers.map(p => (
                      <tr key={p.printer_id} data-testid={`printer-row-${p.printer_id}`} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ ...td, color: 'var(--text-1)' }}>{p.name}</td>
                        <td style={td}>{p.completed}</td>
                        <td style={td}>{p.failed}</td>
                        <td style={td}>{fmtRate(p.success_rate)}</td>
                        <td style={td}>{fmtHours(p.print_seconds)}</td>
                        <td style={td}>
                          <div className="row gap-2" style={{ alignItems: 'center' }}>
                            <div style={{ flex: 1 }}><Progress value={p.utilization_pct} /></div>
                            <span className="num" style={{ minWidth: 44, textAlign: 'right' }}>{p.utilization_pct}%</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card style={{ padding: 20 }}>
            <SectionHeader title="Material" sub="Filament used by material type" />
            {data.materials.length === 0 ? (
              <div className="muted small">No filament usage recorded in this range.</div>
            ) : (
              <div className="col gap-2">
                {data.materials.map(m => (
                  <div key={m.material} data-testid={`material-${m.material}`} className="row gap-3" style={{ alignItems: 'center' }}>
                    <span style={{ width: 80, color: 'var(--text-1)' }}>{m.material}</span>
                    <div style={{ flex: 1 }}><Progress value={(m.grams / maxGrams) * 100} /></div>
                    <span className="num" style={{ minWidth: 150, textAlign: 'right', color: 'var(--text-2)' }}>
                      {fmtGrams(m.grams)} · {m.jobs} {m.jobs === 1 ? 'job' : 'jobs'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
