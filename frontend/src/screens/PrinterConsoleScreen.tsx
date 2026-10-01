import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useFleetRaw, type FleetPrinter } from '../api/fleet';
import {
  jogAxis, homePrinter, setNozzleTemp, setBedTemp, setChamberTemp, uploadToPrinter, type Axis,
} from '../api/printers';
import { Icons } from '../components/icons';
import { TempChart } from '../components/TempChart';
import { appendSample, HISTORY_WINDOW_MS, type TempSample } from '../lib/tempHistory';

const STEPS = [0.1, 1, 10, 50];

function sampleOf(p: FleetPrinter): TempSample {
  const t = p.temperatures ?? {};
  return { t: Date.now(), nozzle: t.nozzle ?? null, bed: t.bed ?? null, chamber: t.chamber ?? null };
}

function SetpointRow({ label, current, target, onSet, disabled }: {
  label: string; current: number | undefined; target: number | undefined;
  onSet: (celsius: number) => Promise<void>; disabled: boolean;
}) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (c: number) => { setBusy(true); try { await onSet(c); } finally { setBusy(false); } };
  return (
    <div className="row gap-3" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
      <div style={{ width: 90 }} className="small">{label}</div>
      <div className="num small" style={{ width: 120 }}>
        {current != null ? `${current.toFixed(0)}°C` : '—'}
        {target !== undefined && <span className="muted"> → {target ? `${target.toFixed(0)}°C` : 'off'}</span>}
      </div>
      <input type="number" min="0" className="input" aria-label={`${label} setpoint`} placeholder="°C"
             style={{ maxWidth: 90 }} value={value} onChange={e => setValue(e.target.value)} />
      <button className="btn sm" disabled={disabled || busy || value.trim() === '' || Number.isNaN(Number(value))}
              onClick={() => run(Number(value))}>Set</button>
      <button className="btn ghost sm" disabled={disabled || busy} onClick={() => run(0)}>Off</button>
    </div>
  );
}

export function PrinterConsoleScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [fleet] = useFleetRaw();
  const printers = useMemo(() => [...fleet].sort((a, b) => a.id - b.id), [fleet]);
  const idx = printers.findIndex(p => String(p.id) === id);
  const printer = idx >= 0 ? printers[idx] : undefined;

  const [step, setStep] = useState(10);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [history, setHistory] = useState<TempSample[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const latest = useRef<FleetPrinter | undefined>(undefined);
  latest.current = printer;

  // A different printer starts a fresh chart.
  useEffect(() => { setHistory([]); setNotice(null); setFile(null); }, [id]);

  // Sample on every telemetry change, and every 10 s so an idle printer still draws a line.
  useEffect(() => {
    if (printer) setHistory(h => appendSample(h, sampleOf(printer)));
  }, [printer?.temperatures]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const timer = setInterval(() => {
      if (latest.current) setHistory(h => appendSample(h, sampleOf(latest.current!)));
    }, 10_000);
    return () => clearInterval(timer);
  }, [id]);

  if (!printer) {
    return (
      <div className="page">
        <div className="muted" style={{ padding: 24 }}>
          {fleet.length === 0 ? 'Loading printer…' : 'Printer not found.'}{' '}
          <button className="btn ghost sm" onClick={() => navigate('/fleet')}>Back to Fleet</button>
        </div>
      </div>
    );
  }

  const caps = printer.capabilities ?? {};
  const printing = printer.state === 'RUNNING' || printer.state === 'PAUSE';
  const offline = !printer.connected;
  const motionDisabled = offline || printing;
  const temps = printer.temperatures ?? {};

  async function act(label: string, fn: () => Promise<unknown>) {
    setNotice(null);
    try {
      await fn();
      setNotice({ kind: 'ok', text: `${label} sent` });
    } catch (e) {
      setNotice({ kind: 'err', text: `${label} failed: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  const go = (delta: number) => {
    const next = printers[(idx + delta + printers.length) % printers.length];
    navigate(`/fleet/${next.id}/console`);
  };

  async function upload(start: boolean) {
    if (!file || uploading) return;
    if (start && !window.confirm(`Start printing ${file.name} on ${printer!.name} now? This bypasses the queue.`)) return;
    setUploading(true);
    setNotice(null);
    try {
      const r = await uploadToPrinter(printer!.id, file, start);
      setNotice({ kind: 'ok', text: r.started ? `Uploaded and started ${r.filename}` : `Uploaded ${r.filename}` });
      setFile(null);
    } catch (e) {
      setNotice({ kind: 'err', text: `Upload failed: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setUploading(false);
    }
  }

  const jog = (axis: Axis, sign: 1 | -1) =>
    act(`Jog ${axis}${sign > 0 ? '+' : '−'}${step}`, () => jogAxis(printer.id, axis, sign * step));

  return (
    <div className="page" data-testid="printer-console">
      <div className="row gap-3" style={{ alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <button className="btn ghost sm" onClick={() => navigate('/fleet')}>{Icons.chevL} Fleet</button>
        <button className="btn icon sm" aria-label="Previous printer" disabled={printers.length < 2} onClick={() => go(-1)}>{Icons.chevL}</button>
        <h2 style={{ margin: 0 }}>{printer.name}</h2>
        <button className="btn icon sm" aria-label="Next printer" disabled={printers.length < 2} onClick={() => go(1)}>{Icons.chevR}</button>
        <span className="small muted">
          {offline ? 'offline' : printing ? 'printing — motion is locked' : 'ready'}
          {printers.length > 1 ? ` · ${idx + 1} of ${printers.length}` : ''}
        </span>
      </div>

      {notice && (
        <div role={notice.kind === 'err' ? 'alert' : 'status'} className="small"
             style={{ marginBottom: 12, color: notice.kind === 'err' ? 'var(--err)' : 'var(--ok)' }}>
          {notice.text}
        </div>
      )}

      <div className="col gap-4">
        <div className="card" style={{ padding: 20 }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Motion</div>
          <div className="row gap-2" style={{ marginBottom: 12, alignItems: 'center' }}>
            <span className="small muted">Step</span>
            {STEPS.map(s => (
              <button key={s} className={`btn sm${s === step ? ' primary' : ''}`} aria-pressed={s === step}
                      onClick={() => setStep(s)}>{s} mm</button>
            ))}
          </div>
          <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
            {caps.axis_jog && (['X', 'Y'] as Axis[]).map(a => (
              <span key={a} className="row gap-1">
                <button className="btn sm" disabled={motionDisabled} onClick={() => jog(a, -1)}>{a}−</button>
                <button className="btn sm" disabled={motionDisabled} onClick={() => jog(a, 1)}>{a}+</button>
              </span>
            ))}
            <span className="row gap-1">
              <button className="btn sm" disabled={motionDisabled} onClick={() => jog('Z', -1)}>Z−</button>
              <button className="btn sm" disabled={motionDisabled} onClick={() => jog('Z', 1)}>Z+</button>
            </span>
          </div>
          <div className="row gap-2" style={{ marginTop: 12, flexWrap: 'wrap' }}>
            <button className="btn sm" disabled={motionDisabled} onClick={() => act('Home all', () => homePrinter(printer.id, 'all'))}>Home all</button>
            {caps.home_axes && (['X', 'Y', 'Z'] as Axis[]).map(a => (
              <button key={a} className="btn ghost sm" disabled={motionDisabled}
                      onClick={() => act(`Home ${a}`, () => homePrinter(printer.id, a))}>Home {a}</button>
            ))}
          </div>
          {!caps.axis_jog && (
            <div className="tiny muted" style={{ marginTop: 8 }}>This printer type only supports Z jog and home-all from here.</div>
          )}
        </div>

        <div className="card" style={{ padding: 20 }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Temperatures</div>
          <div className="col gap-2">
            {caps.nozzle_temp && (
              <SetpointRow key={`n${printer.id}`} label="Nozzle" current={temps.nozzle} target={temps.nozzle_target} disabled={motionDisabled}
                           onSet={c => act(`Nozzle ${c}°C`, () => setNozzleTemp(printer.id, c))} />
            )}
            {caps.temp_control && (
              <SetpointRow key={`b${printer.id}`} label="Bed" current={temps.bed} target={temps.bed_target} disabled={offline}
                           onSet={c => act(`Bed ${c}°C`, () => setBedTemp(String(printer.id), c))} />
            )}
            {caps.chamber_temp && (
              <SetpointRow key={`c${printer.id}`} label="Chamber" current={temps.chamber} target={temps.chamber_target} disabled={offline}
                           onSet={c => act(`Chamber ${c}°C`, () => setChamberTemp(printer.id, c))} />
            )}
            {!caps.nozzle_temp && !caps.temp_control && !caps.chamber_temp && (
              <div className="small muted">This printer type has no temperature setpoints available here.</div>
            )}
          </div>
          <div style={{ marginTop: 16 }}>
            <TempChart history={history} windowMs={HISTORY_WINDOW_MS} />
          </div>
        </div>

        {caps.direct_upload && (
          <div className="card" style={{ padding: 20 }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Send a file</div>
            <div className="row gap-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <input type="file" aria-label="File to send" accept=".gcode,.3mf,.bgcode"
                     onChange={e => setFile(e.target.files?.[0] ?? null)} />
              <button className="btn sm" disabled={!file || uploading || offline} onClick={() => upload(false)}>
                {Icons.upload} Upload only
              </button>
              <button className="btn primary sm" disabled={!file || uploading || offline || printing} onClick={() => upload(true)}>
                {Icons.play} Upload &amp; print
              </button>
            </div>
            <div className="tiny muted" style={{ marginTop: 8 }}>
              Goes straight to the printer, bypassing the queue (.gcode, .3mf, .bgcode; up to 300 MB). Printing this way
              marks the plate as not ready, like a queued print.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
