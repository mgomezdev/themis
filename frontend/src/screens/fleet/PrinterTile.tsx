import React, { useState } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { fmtTime } from '../../data/helpers';
import { StatusPill, Progress, VideoTile, Swatch } from '../../components/ui';
import { Icons } from '../../components/icons';
import { SEVERITY_COLOR } from '../../lib/severity';
import type { Printer } from '../../data/types';
import { markPlateCleared, getPlateResults, type PlateResults } from '../../api/printers';
import { PrintResultsModal } from '../../components/PrintResultsModal';
import { type MaintenanceStatusRow } from '../../api/maintenance';
import { DueMaintenanceHat } from '../../components/DueMaintenanceHat';
import { QueueStateBadges, SetupIncompleteCard } from '../../components/FleetSetup';
import { cardCueStyle } from './cardCue';

// Fleet: a printer as a card tile or a table row, with its status badges and the ready-for-work button.

export function DormantBadge({ reason }: { reason: string }) {
  return (
    <span className="tiny" title={reason} style={{
      padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap',
      background: 'color-mix(in oklch, var(--idle) 18%, transparent)', color: 'var(--text-3)', fontWeight: 600,
    }}>DORMANT</span>
  );
}

export function ReadyForWorkButton({ printerId, refetchFleet, block }: {
  printerId: string; refetchFleet: () => void; block?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<PlateResults | null>(null);
  return (
    <>
      <button
        className="btn primary sm"
        style={block ? { width: '100%' } : undefined}
        disabled={busy}
        onClick={async (e) => {
          e.stopPropagation();
          setBusy(true);
          try {
            // A finished project job asks what printed first (BIZ-364); anything else is a plain clear.
            const pending = await getPlateResults(printerId).catch(() => null);
            if (pending?.job_id != null && pending.parts.length > 0) { setAsking(pending); return; }
            await markPlateCleared(printerId);
            refetchFleet();
          } finally { setBusy(false); }
        }}>
        {Icons.check} Ready for new work
      </button>
      {asking && (
        <PrintResultsModal printerId={printerId} pending={asking} onClose={() => setAsking(null)}
                           onDone={() => { setAsking(null); refetchFleet(); }} />
      )}
    </>
  );
}

// ── PrinterTile ───────────────────────────────────────────────────────────────
/** Unacknowledged-alarm badge; links to that printer's alarms. */
/** BIZ-365: a printer that collected enough waived parts to flag (wash the plate / do maintenance) or stop. */
export function WaiverBadge({ printer: p }: { printer: Printer }) {
  if (!p.waiverFlag && !p.waiverStopped) return null;
  const n = p.waivedParts ?? 0;
  return (
    <span data-testid="waiver-badge" onClick={e => e.stopPropagation()}
          title={`${n} waived part${n === 1 ? '' : 's'}: ${p.waiverStopped ? 'not taking queue work' : 'consider a plate wash or maintenance'}`}
          className={`pill ${p.waiverStopped ? 'err' : 'warn'}`} style={{ fontSize: 11 }}>
      {p.waiverStopped ? 'Stopped' : 'Check'}: {n} waived
    </span>
  );
}

export function AlarmBadge({ printer: p }: { printer: Printer }) {
  const inRouter = useInRouterContext();
  if (!p.alarmCount || !p.alarmSeverity) return null;
  const props = {
    onClick: (e: React.MouseEvent) => e.stopPropagation(), 'data-testid': 'alarm-badge',
    title: `${p.alarmCount} active alarm${p.alarmCount === 1 ? '' : 's'} (worst: ${p.alarmSeverity})`,
    style: { color: SEVERITY_COLOR[p.alarmSeverity], border: `1px solid ${SEVERITY_COLOR[p.alarmSeverity]}`, borderRadius: 999,
             padding: '1px 8px', fontSize: 11, fontWeight: 600, textDecoration: 'none' },
  };
  const to = `/alarms?printer=${p.id}`;
  return inRouter
    ? <Link to={to} {...props}>{Icons.alert} {p.alarmCount}</Link>      // client-side navigation: keeps the SPA state
    : <a href={to} {...props}>{Icons.alert} {p.alarmCount}</a>;
}

export function PrinterTile({ printer: p, onClick, refetchFleet, snapshotIntervalMs, dueRowsByPrinter }: { printer: Printer; onClick: () => void; refetchFleet: () => void; snapshotIntervalMs?: number; dueRowsByPrinter: Record<string, MaintenanceStatusRow[]> }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{ cursor: 'pointer', padding: 0, overflow: 'hidden', transition: 'border-color 120ms ease', ...cardCueStyle(p) }}>
      <div className="row between" style={{ padding: '12px 14px 8px' }}>
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
        <div className="row gap-2">
          {p.dormantReason && <DormantBadge reason={p.dormantReason} />}
          <QueueStateBadges printer={p} />
          <AlarmBadge printer={p} />
            <WaiverBadge printer={p} />
          <StatusPill status={p.status} />
        </div>
      </div>
      <SetupIncompleteCard printer={p} refetchFleet={refetchFleet} />
      <div style={{ padding: '0 14px' }}>
        <VideoTile
          live={p.status !== 'offline'}
          status={p.status}
          printerId={p.id}
          intervalMs={snapshotIntervalMs}
          noSnapshotsWhileIdle={p.noSnapshotsWhileIdle}
        />
      </div>
      <div className="row between" style={{ padding: '12px 14px 10px' }}>
        <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
          <Swatch color={p.material.color} />
          <div className="col min-0">
            <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
            <div className="tiny muted">{p.material.type}</div>
          </div>
        </div>
        {isPrinting ? (
          <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap' }}>
            <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
              {fmtTime(p.timeRemaining)}
            </div>
            <div className="tiny muted">remaining</div>
          </div>
        ) : (
          <div className="tiny muted" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{p.note ?? '—'}</div>
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
      {p.awaitingPlateClear && (
        <div style={{ padding: '0 14px 14px' }}>
          <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} block />
          <div className="tiny muted" style={{ textAlign: 'center', marginTop: 4 }}>
            Clear the plate, then mark ready
          </div>
        </div>
      )}
    </div>
  );
}

// ── PrinterRow (rows layout — no video feed) ──────────────────────────────────
export function PrinterRow({ printer: p, expanded, onClick, refetchFleet, dueRowsByPrinter }: { printer: Printer; expanded: boolean; onClick: () => void; refetchFleet: () => void; dueRowsByPrinter: Record<string, MaintenanceStatusRow[]> }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{
      cursor: 'pointer', padding: '12px 14px',
      display: 'grid',
      gridTemplateColumns: 'auto auto 1.4fr auto 1.6fr 1fr auto',
      alignItems: 'center', gap: 16,
      background: expanded ? 'var(--bg-2)' : undefined,
      ...(expanded ? { borderColor: 'var(--accent)' } : cardCueStyle(p)),
    }}>
      <span style={{ display: 'inline-flex', color: 'var(--text-3)', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease' }}>
        {Icons.chevR}
      </span>
      <div style={{
        width: 36, height: 36, borderRadius: 6,
        background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
        border: '1px solid var(--border-1)', display: 'grid', placeItems: 'center',
      }}>
        <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
      </div>
      <div className="col min-0">
        <div className="row gap-1">
          <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
          <div style={{ fontWeight: 600, fontSize: 14 }}>{p.nickname}</div>
        </div>
        <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
      </div>
      <div className="col gap-1" style={{ alignItems: 'flex-start' }}>
        <StatusPill status={p.status} />
        {p.dormantReason && <DormantBadge reason={p.dormantReason} />}
        <QueueStateBadges printer={p} />
      </div>
      <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
        <Swatch color={p.material.color} />
        <div className="col min-0">
          <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
          <div className="tiny muted">{p.material.type}</div>
        </div>
      </div>
      <div className="col min-0">
        {isPrinting ? (
          <>
            <Progress value={p.progress} />
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="mono tiny muted">{p.currentJobId}</span>
              <span className="num tiny muted">{p.progress}%</span>
            </div>
          </>
        ) : (
          <span className="tiny muted">{p.note ?? '—'}</span>
        )}
      </div>
      {p.awaitingPlateClear ? (
        <div style={{ justifySelf: 'end' }} onClick={e => e.stopPropagation()}>
          <ReadyForWorkButton printerId={p.id} refetchFleet={refetchFleet} />
        </div>
      ) : isPrinting ? (
        <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap', minWidth: 80 }}>
          <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
            {fmtTime(p.timeRemaining)}
          </div>
          <div className="tiny muted">remaining</div>
        </div>
      ) : (
        <div className="tiny muted" style={{ minWidth: 80, textAlign: 'right' }}>—</div>
      )}
      {p.configured === false && (
        <div style={{ gridColumn: '1 / -1' }}>
          <SetupIncompleteCard printer={p} refetchFleet={refetchFleet} />
        </div>
      )}
    </div>
  );
}

// ── AddPrinterCard ────────────────────────────────────────────────────────────
export function AddPrinterCard({ onClick, variant }: { onClick: () => void; variant: 'card' | 'row' }) {
  const hoverOn = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--accent)';
    e.currentTarget.style.color = 'var(--accent-hi)';
    e.currentTarget.style.background = 'var(--accent-glow)';
  };
  const hoverOff = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--border-2)';
    e.currentTarget.style.color = 'var(--text-3)';
    e.currentTarget.style.background = 'transparent';
  };

  if (variant === 'row') {
    return (
      <button onClick={onClick} className="row gap-3"
              style={{ padding: '14px 18px', borderRadius: 10, width: '100%',
                       border: '1.5px dashed var(--border-2)', background: 'transparent',
                       color: 'var(--text-3)', cursor: 'pointer', alignItems: 'center',
                       justifyContent: 'center', transition: 'all 140ms ease' }}
              onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
        {Icons.plus}
        <span className="small fw-500">Add printer</span>
      </button>
    );
  }
  return (
    <button onClick={onClick} className="col"
            style={{ alignItems: 'center', justifyContent: 'center', gap: 8,
                     minHeight: 280, borderRadius: 10, padding: 14,
                     border: '1.5px dashed var(--border-2)', background: 'transparent',
                     color: 'var(--text-3)', cursor: 'pointer', transition: 'all 140ms ease' }}
            onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
      <div style={{ width: 44, height: 44, borderRadius: 12, display: 'grid', placeItems: 'center',
                    background: 'var(--bg-2)', border: '1px solid var(--border-1)' }}>
        {Icons.plus}
      </div>
      <div className="small fw-500">Add printer</div>
      <div className="tiny muted" style={{ textAlign: 'center', maxWidth: 220 }}>
        Connect a Bambu, Elegoo or Snapmaker host.
      </div>
    </button>
  );
}
