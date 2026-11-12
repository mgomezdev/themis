// Fleet: the expanded printer card (camera, telemetry, controls, maintenance).
import { useState, useEffect } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { fmtTime } from '../../data/helpers';
import { StatusPill, Progress, VideoTile, Swatch, Kv } from '../../components/ui';
import { Icons } from '../../components/icons';
import type { Printer } from '../../data/types';
import { pausePrinter, resumePrinter, stopPrinter, updatePrinter, reconnectPrinter, type PrinterType } from '../../api/printers';
import { type MaintenanceStatusRow } from '../../api/maintenance';
import { DueMaintenanceHat } from '../../components/DueMaintenanceHat';
import { completeMaintenanceItem, type FleetVendorModel } from '../../api/maintenance';
import { EditPrinterModal } from './EditPrinterModal';
import { FilamentPicker } from './FilamentPicker';
import { QuickAddMaintenanceModal } from './QuickAddMaintenanceModal';
import { AlarmBadge, DormantBadge, ReadyForWorkButton, WaiverBadge } from './PrinterTile';
import { cardCueStyle } from './cardCue';

// ── Telem row ────────────────────────────────────────────────────────────────
function Telem({ label, value, target, tone }: { label: string; value: string; target: string; tone?: string | null }) {
  return (
    <div className="row between">
      <div className="small muted">{label}</div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: tone === 'warn' ? 'var(--warn)' : 'var(--text-1)' }}>
          {value}
        </span>
        <span className="num tiny muted">/ {target}</span>
      </div>
    </div>
  );
}

// ── Fan icon (spins proportional to duty cycle) ───────────────────────────────
function FanIcon({ pct }: { pct: number }) {
  const running = pct > 0;
  const period = running ? Math.max(0.16, 1.8 - (pct / 100) * 1.6) : 0;
  return (
    <svg width="16" height="16" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round"
         style={{
           color: running ? 'var(--accent-hi)' : 'var(--text-4)',
           animation: running ? `spin ${period.toFixed(2)}s linear infinite` : 'none',
           transformOrigin: 'center',
           flexShrink: 0,
         }}>
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <path d="M12 10.5 C 12 6.5, 14.5 4, 18.5 4 C 18.5 8, 16 10.5, 12 10.5 Z" />
      <path d="M13.5 12 C 17.5 12, 20 14.5, 20 18.5 C 16 18.5, 13.5 16, 13.5 12 Z" />
      <path d="M12 13.5 C 12 17.5, 9.5 20, 5.5 20 C 5.5 16, 8 13.5, 12 13.5 Z" />
      <path d="M10.5 12 C 6.5 12, 4 9.5, 4 5.5 C 8 5.5, 10.5 8, 10.5 12 Z" />
    </svg>
  );
}

function FanTelem({ label, pct, maxRpm = 7000 }: { label: string; pct: number; maxRpm?: number }) {
  const running = pct > 0;
  const rpm = Math.round((pct / 100) * maxRpm);
  return (
    <div className="row between">
      <div className="row gap-2">
        <FanIcon pct={pct} />
        <span className="small muted">{label}</span>
      </div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: running ? 'var(--text-1)' : 'var(--text-3)' }}>
          {pct}%
        </span>
        <span className="num tiny muted">{rpm} rpm</span>
      </div>
    </div>
  );
}

/** Link to the printer console (a plain anchor when rendered outside a router, e.g. in isolated tests). */
function ConsoleLink({ printerId }: { printerId: string }) {
  const inRouter = useInRouterContext();
  const href = `/fleet/${printerId}/console`;
  const content = <>{Icons.printer} Console</>;
  return inRouter
    ? <Link className="btn sm" to={href} title="Open the printer console">{content}</Link>
    : <a className="btn sm" href={href} title="Open the printer console">{content}</a>;
}

// ── Edit printer modal ───────────────────────────────────────────────────────

// ── PrinterExpandedCard ───────────────────────────────────────────────────────
export function PrinterExpandedCard({ printer: p, printerTypes, refetchFleet, onCollapse, snapshotIntervalMs, dueRowsByPrinter, refetchMaintenance, fleetModels }: {
  printer: Printer;
  printerTypes: PrinterType[];
  refetchFleet: () => void;
  onCollapse: () => void;
  snapshotIntervalMs?: number;
  dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>;
  refetchMaintenance: () => void;
  fleetModels: FleetVendorModel[];
}) {
  const isPrinting = p.status === 'printing';
  const isPaused = p.status === 'paused';
  const isOffline = p.status === 'offline';
  const [nickname, setNickname] = useState(p.nickname);
  const [editingName, setEditingName] = useState(false);
  const [editingPrinter, setEditingPrinter] = useState(false);
  const [pickingFilament, setPickingFilament] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [addingMaintenance, setAddingMaintenance] = useState(false);

  useEffect(() => { setNickname(p.nickname); }, [p.nickname]);

  const mats = p.materials ?? [p.material];

  return (
    <>
      <div className="card" style={{
        padding: 0, overflow: 'hidden',
        borderColor: 'var(--border-3)',
        boxShadow: '0 0 0 1px var(--accent-glow), 0 18px 40px -20px rgba(0,0,0,0.6)',
        ...cardCueStyle(p),
      }}>
        {/* Header */}
        <div className="row between wrap" style={{
          padding: '14px 18px', background: 'var(--bg-2)',
          borderBottom: '1px solid var(--border-1)', gap: 12, alignItems: 'center',
        }}>
          <div className="col" style={{ minWidth: 0, flex: '1 1 220px' }}>
            <div className="row gap-2 wrap" style={{ alignItems: 'baseline' }}>
              <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
              {editingName ? (
                <input autoFocus className="input" value={nickname}
                       onChange={e => setNickname(e.target.value)}
                       onBlur={() => setEditingName(false)}
                       onKeyDown={e => { if (e.key === 'Enter' || e.key === 'Escape') setEditingName(false); }}
                       style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', padding: '2px 8px', width: 220, background: 'var(--bg-0)' }} />
              ) : (
                <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em' }}>{nickname}</div>
              )}
              <button className="btn ghost icon sm" title="Rename" onClick={() => setEditingName(v => !v)}>
                <svg className="ico" width="14" height="14" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9"/>
                  <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
                </svg>
              </button>
              <div className="muted small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{p.name}</div>
              <span className="mono tiny muted shrink-0">· {p.id}</span>
            </div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <span className="num">{p.buildVolume}</span> mm · {p.chamber ? 'enclosed' : 'open frame'} · capable: {p.capabilities.join(' · ')}
            </div>
          </div>
          <div className="row gap-2 wrap">
            {p.dormantReason && <DormantBadge reason={p.dormantReason} />}
            <AlarmBadge printer={p} />
            <WaiverBadge printer={p} />
            <StatusPill status={p.status} />
            {isOffline && (
              <button
                className="btn sm"
                disabled={reconnecting}
                title="Attempt to reconnect this printer"
                onClick={async () => {
                  setReconnecting(true);
                  try { await reconnectPrinter(p.id); refetchFleet(); }
                  catch { /* connection attempt launched — fleet will update via WS */ }
                  finally { setReconnecting(false); }
                }}>
                {reconnecting ? 'Connecting…' : <>{Icons.refresh} Reconnect</>}
              </button>
            )}
            {p.awaitingPlateClear && (
              <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} />
            )}
            <button
              className={`btn sm${p.queueOn ? '' : ' ghost'}`}
              title={p.queueOn ? 'Disable queue pulling' : 'Enable queue pulling'}
              style={p.queueOn ? {} : { color: 'var(--text-3)' }}
              onClick={async () => {
                await updatePrinter(Number(p.id), { queue_on: !p.queueOn });
                refetchFleet();
              }}>
              {p.queueOn ? <>{Icons.queue} Queue on</> : <>{Icons.queue} Queue off</>}
            </button>
            <button className="btn sm">{Icons.camera} Snapshot</button>
            <ConsoleLink printerId={p.id} />
            <button className="btn icon sm" title="Add maintenance item" onClick={() => setAddingMaintenance(true)}>
              👷
            </button>
            <button className="btn icon sm" title="Edit printer" onClick={() => setEditingPrinter(true)}>
              {Icons.wrench}
            </button>
            <button className="btn ghost icon sm" title="Collapse" onClick={onCollapse}>{Icons.x}</button>
          </div>
        </div>

        {/* Body */}
        <div className="fleet-expanded">
          {/* LEFT */}
          <div className="col gap-4 min-0">
            <VideoTile
              live={p.status !== 'offline'}
              status={p.status}
              time={isPrinting ? p.timeElapsed : undefined}
              printerId={p.id}
              intervalMs={snapshotIntervalMs}
              noSnapshotsWhileIdle={p.noSnapshotsWhileIdle}
            />

            <div className="row gap-6 wrap">
              <Kv k="Progress" v={<span className="num" style={{ fontSize: 22, fontWeight: 600 }}>{p.progress}%</span>} />
              <Kv k="Time left" v={
                <span className="num" style={{ fontSize: 22, fontWeight: 600, color: isPrinting ? 'var(--accent-hi)' : 'var(--text-3)' }}>
                  {isPrinting ? fmtTime(p.timeRemaining) : '—'}
                </span>
              } />
              <Kv k="Elapsed" v={<span className="num" style={{ fontSize: 22 }}>{isPrinting ? fmtTime(p.timeElapsed) : '—'}</span>} />
              {p.layer && (
                <Kv k="Layer" v={
                  <span className="num" style={{ fontSize: 22 }}>
                    {p.layer.now}<span className="muted" style={{ fontSize: 14 }}> / {p.layer.total}</span>
                  </span>
                } />
              )}
            </div>
            {isPrinting && <Progress value={p.progress} large />}

            <div className="row gap-2" style={{ marginTop: 2 }}>
              {isPrinting && (
                <>
                  <button className="btn" onClick={() => pausePrinter(p.id).catch(console.error)}>{Icons.pause} Pause</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {isPaused && (
                <>
                  <button className="btn" onClick={() => resumePrinter(p.id).catch(console.error)}>{Icons.play} Resume</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {!isPrinting && !isPaused && (
                <button className="btn primary">{Icons.play} Claim next from queue</button>
              )}
            </div>

          </div>

          {/* RIGHT */}
          <div className="col gap-4">
            <div className="card" style={{ padding: 14, background: 'var(--bg-0)' }}>
              <div className="row between">
                <span className="tag-key">Loaded filament{mats.length > 1 ? 's' : ''}</span>
                <button className="btn ghost sm" onClick={() => setPickingFilament(v => !v)}>
                  {pickingFilament ? 'Cancel' : 'Change'}
                </button>
              </div>
              {mats.length === 0 ? (
                <div className="tiny muted" style={{ marginTop: 10 }}>— no filament loaded</div>
              ) : (
                <div className="col gap-2" style={{ marginTop: 10 }}>
                  {mats.map((m, i) => (
                    <div key={i} className="row gap-3">
                      <Swatch color={m.color} large={mats.length === 1} />
                      <div className="col" style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {m.name}
                        </div>
                        <div className="tiny muted">{m.type}</div>
                      </div>
                      {mats.length > 1 && <span className="tiny muted mono shrink-0">T{i}</span>}
                    </div>
                  ))}
                </div>
              )}
              {pickingFilament && (
                <FilamentPicker
                  printerId={Number(p.id)}
                  onClose={() => setPickingFilament(false)}
                  onSaved={() => { setPickingFilament(false); refetchFleet(); }}
                />
              )}
            </div>

            <div className="card" style={{ padding: 14, background: 'var(--bg-0)' }}>
              <div className="tag-key" style={{ marginBottom: 10 }}>Temperatures</div>
              <div className="col gap-3">
                {p.nozzles && p.nozzles.length > 1
                  ? p.nozzles.map(n => (
                      <Telem key={n.index} label={`Nozzle T${n.index}`} value={`${n.temp}°C`}
                             target={n.target > 0 ? `${n.target}°C` : '—'} tone={n.target > 0 ? 'warn' : null} />
                    ))
                  : <Telem label="Nozzle" value={`${p.nozzleTemp}°C`}
                           target={p.nozzleTempTarget ? `${p.nozzleTempTarget}°C` : '—'} tone={p.nozzleTempTarget ? 'warn' : null} />}
                <Telem label="Bed" value={`${p.bedTemp}°C`} target={p.bedTempTarget ? `${p.bedTempTarget}°C` : '—'} />
                {p.chamberTemp != null && (
                  <Telem label="Chamber" value={`${p.chamberTemp}°C`} target={p.chamber ? '60°C' : '—'} />
                )}
              </div>

              <div className="tag-key" style={{ marginTop: 16, marginBottom: 10 }}>Fans</div>
              <div className="col gap-3">
                <FanTelem label="Model" pct={p.fanModel} maxRpm={7200} />
                <FanTelem label="Auxiliary" pct={p.fanAux} maxRpm={5400} />
                <FanTelem label="Box" pct={p.fanBox} maxRpm={5400} />
              </div>
            </div>

            {(dueRowsByPrinter[p.id] ?? []).length > 0 && (
              <div className="card" style={{ padding: 14, background: 'var(--bg-0)', borderColor: 'var(--warn)' }}>
                <div className="tag-key" style={{ marginBottom: 10 }}>👷 Maintenance due</div>
                <div className="col gap-2">
                  {(dueRowsByPrinter[p.id] ?? []).map(row => (
                    <div key={row.item_id} className="row between">
                      <div style={{ fontWeight: 500, fontSize: 13 }}>{row.item_name}</div>
                      <button
                        className="btn sm"
                        onClick={() => {
                          completeMaintenanceItem(Number(p.id), row.item_id)
                            .then(refetchMaintenance)
                            .catch(console.error);
                        }}
                      >
                        Acknowledge
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {editingPrinter && (
        <EditPrinterModal
          printer={p} printerTypes={printerTypes}
          onSaved={() => { setEditingPrinter(false); refetchFleet(); }}
          onDeleted={() => { setEditingPrinter(false); refetchFleet(); onCollapse(); }}
          onClose={() => setEditingPrinter(false)}
        />
      )}

      {addingMaintenance && (
        <QuickAddMaintenanceModal
          printer={p}
          fleetModels={fleetModels}
          onClose={() => setAddingMaintenance(false)}
          onSaved={() => { setAddingMaintenance(false); refetchMaintenance(); }}
        />
      )}
    </>
  );
}

// ── Shared printer-card affordances ───────────────────────────────────────────
