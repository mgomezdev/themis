import React, { useState, useEffect } from 'react';
import { useFleetData } from '../api/fleet';
import { Icons } from '../components/icons';
import type { Printer } from '../data/types';
import { fetchPrinterTypes, type PrinterType } from '../api/printers';
import { CAP, useInventory } from '../api/inventory';
import { getQueueConfig } from '../api/queue';
import { PrinterAddForm } from './PrintersScreen';
import { useMaintenanceStatus, type MaintenanceStatusRow } from '../api/maintenance';
import { useMediaQuery } from '../components/useMediaQuery';
import { ScanSpoolModal } from '../components/ScanSpoolModal';
import { useFleetVendorModels, type FleetVendorModel } from '../api/maintenance';
import { PrinterExpandedCard } from './fleet/PrinterExpandedCard';
import { AddPrinterCard, PrinterRow, PrinterTile } from './fleet/PrinterTile';

type Layout = 'cards' | 'rows';
type Media = 'camera' | 'image';
const MEDIA_KEY = 'themis.fleet.media';

function loadMedia(): Media {
  try { return localStorage.getItem(MEDIA_KEY) === 'image' ? 'image' : 'camera'; } catch { return 'camera'; }
}

// ── Media toggle (Camera / Image on the cards) ────────────────────────────────
function MediaToggle({ value, onChange }: { value: Media; onChange: (v: Media) => void }) {
  const opts: { id: Media; label: string; icon: React.ReactElement }[] = [
    { id: 'camera', label: 'Camera', icon: Icons.camera },
    { id: 'image',  label: 'Image',  icon: Icons.printer },
  ];
  return (
    <div className="row layout-toggle" role="group" aria-label="Card media" style={{ gap: 0, padding: 2, borderRadius: 7, background: 'var(--bg-1)', border: '1px solid var(--border-1)' }}>
      {opts.map(o => {
        const on = value === o.id;
        return (
          <button key={o.id} onClick={() => onChange(o.id)} className="row gap-2" title={o.label} aria-pressed={on}
                  style={{
                    padding: '4px 10px', borderRadius: 5, border: 'none',
                    background: on ? 'var(--bg-2)' : 'transparent',
                    color: on ? 'var(--text-1)' : 'var(--text-3)',
                    fontSize: 12, fontWeight: 500, cursor: 'pointer', alignItems: 'center',
                    boxShadow: on ? '0 0 0 1px var(--border-2)' : 'none',
                  }}>
            {React.cloneElement(o.icon, { size: 13 } as React.SVGProps<SVGSVGElement>)}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Layout toggle (Cards / Rows) ──────────────────────────────────────────────
function LayoutToggle({ value, onChange }: { value: Layout; onChange: (v: Layout) => void }) {
  const opts: { id: Layout; label: string; icon: React.ReactElement }[] = [
    { id: 'cards', label: 'Cards', icon: Icons.fleet },
    { id: 'rows',  label: 'Rows',  icon: Icons.layers },
  ];
  return (
    <div className="row layout-toggle" style={{ gap: 0, padding: 2, borderRadius: 7, background: 'var(--bg-1)', border: '1px solid var(--border-1)' }}>
      {opts.map(o => {
        const on = value === o.id;
        return (
          <button key={o.id} onClick={() => onChange(o.id)} className="row gap-2" title={o.label}
                  style={{
                    padding: '4px 10px', borderRadius: 5, border: 'none',
                    background: on ? 'var(--bg-2)' : 'transparent',
                    color: on ? 'var(--text-1)' : 'var(--text-3)',
                    fontSize: 12, fontWeight: 500, cursor: 'pointer', alignItems: 'center',
                    boxShadow: on ? '0 1px 0 rgba(0,0,0,0.25), 0 0 0 1px var(--border-2)' : 'none',
                  }}>
            {React.cloneElement(o.icon, { size: 13 } as React.SVGProps<SVGSVGElement>)}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── FleetGrid (cards layout) ──────────────────────────────────────────────────
function FleetGrid({ printers, expandedId, onToggle, onAdd, printerTypes, refetchFleet, snapshotIntervalMs, dueRowsByPrinter, refetchMaintenance, fleetModels, media }: {
  media: Media; printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
  printerTypes: PrinterType[]; refetchFleet: () => void; snapshotIntervalMs?: number;
  dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>; refetchMaintenance: () => void;
  fleetModels: FleetVendorModel[];
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id} style={{ gridColumn: expanded ? '1 / -1' : 'auto' }}>
            {expanded
              ? <PrinterExpandedCard printer={p} printerTypes={printerTypes} refetchFleet={refetchFleet} onCollapse={() => onToggle(p.id)} snapshotIntervalMs={snapshotIntervalMs} dueRowsByPrinter={dueRowsByPrinter} refetchMaintenance={refetchMaintenance} fleetModels={fleetModels} />
              : <PrinterTile printer={p} onClick={() => onToggle(p.id)} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} dueRowsByPrinter={dueRowsByPrinter} media={media} />}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="card" />
    </div>
  );
}

// ── FleetRows (rows layout) ───────────────────────────────────────────────────
function FleetRows({ printers, expandedId, onToggle, onAdd, printerTypes, refetchFleet, snapshotIntervalMs, dueRowsByPrinter, refetchMaintenance, fleetModels }: {
  printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
  printerTypes: PrinterType[]; refetchFleet: () => void; snapshotIntervalMs?: number;
  dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>; refetchMaintenance: () => void;
  fleetModels: FleetVendorModel[];
}) {
  return (
    <div className="col gap-2">
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id}>
            <PrinterRow printer={p} expanded={expanded} onClick={() => onToggle(p.id)} refetchFleet={refetchFleet} dueRowsByPrinter={dueRowsByPrinter} />
            {expanded && (
              <div style={{ marginTop: 8 }}>
                <PrinterExpandedCard printer={p} printerTypes={printerTypes} refetchFleet={refetchFleet} onCollapse={() => onToggle(p.id)} snapshotIntervalMs={snapshotIntervalMs} dueRowsByPrinter={dueRowsByPrinter} refetchMaintenance={refetchMaintenance} fleetModels={fleetModels} />
              </div>
            )}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="row" />
    </div>
  );
}

// ── FleetScreen ───────────────────────────────────────────────────────────────
export function FleetScreen() {
  const [printers, refetchFleet] = useFleetData();
  const [layout, setLayout] = useState<Layout>('cards');
  const [media, setMediaState] = useState<Media>(loadMedia);
  const setMedia = (m: Media) => { setMediaState(m); try { localStorage.setItem(MEDIA_KEY, m); } catch { /* storage blocked: the choice just won't persist */ } };
  // The row table has seven fixed columns; on a phone the card layout is the only one that fits.
  const narrow = useMediaQuery('(max-width: 768px)');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [scanning, setScanning] = useState(false);
  const canScan = useInventory().has(CAP.LABEL_SCAN);
  const [printerTypes, setPrinterTypes] = useState<PrinterType[]>([]);
  const [snapshotIntervalMs, setSnapshotIntervalMs] = useState<number>(2000);
  const { rows: maintenanceRows, refetch: refetchMaintenance } = useMaintenanceStatus();
  const fleetModels = useFleetVendorModels();
  const dueRowsByPrinter = (Array.isArray(maintenanceRows) ? maintenanceRows : []).reduce<Record<string, MaintenanceStatusRow[]>>((acc, r) => {
    if (r.due) {
      const key = String(r.printer_id);
      (acc[key] ??= []).push(r);
    }
    return acc;
  }, {});

  useEffect(() => {
    fetchPrinterTypes().then(setPrinterTypes).catch(console.error);
    getQueueConfig().then(c => setSnapshotIntervalMs((c.snapshot_interval_seconds ?? 2) * 1000)).catch(console.error);
  }, []);

  const toggle = (id: string) => setExpandedId(expandedId === id ? null : id);
  const statusCounts = {
    printing: printers.filter(p => p.status === 'printing').length,
    claiming: printers.filter(p => p.status === 'claiming').length,
    idle:     printers.filter(p => p.status === 'idle').length,
    paused:   printers.filter(p => p.status === 'paused').length,
    error:    printers.filter(p => p.status === 'error').length,
    offline:  printers.filter(p => p.status === 'offline').length,
  };
  const fleetSummary = [
    statusCounts.error    && `${statusCounts.error} error`,
    statusCounts.paused   && `${statusCounts.paused} paused`,
    statusCounts.printing && `${statusCounts.printing} printing`,
    statusCounts.claiming && `${statusCounts.claiming} claiming`,
    statusCounts.idle     && `${statusCounts.idle} idle`,
    statusCounts.offline  && `${statusCounts.offline} offline`,
  ].filter(Boolean).join(' · ');

  if (adding) {
    return (
      <PrinterAddForm
        types={printerTypes}
        backLabel="Fleet"
        onCancel={() => setAdding(false)}
        onCreated={() => { setAdding(false); refetchFleet(); }}
        onPrinterAdded={refetchFleet}
      />
    );
  }

  return (
    <div className="col gap-5">
      <div className="row between wrap">
        <div>
          <div className="tag-key" style={{ marginBottom: 2 }}>Workshop</div>
          <div className="row gap-3 wrap" style={{ alignItems: 'baseline' }}>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              {printers.length} printers
            </div>
            <div className="muted small">{fleetSummary}</div>
          </div>
        </div>
        <div className="row gap-2 wrap">
          {(layout === 'cards' || narrow) && <MediaToggle value={media} onChange={setMedia} />}
          {!narrow && <LayoutToggle value={layout} onChange={setLayout} />}
          <button className="btn sm nowrap mobile-redundant">{Icons.refresh} Sync now</button>
          {canScan && (
            <button className="btn sm nowrap" onClick={() => setScanning(true)}>
              {Icons.spool} Scan spool
            </button>
          )}
          <button className="btn primary sm nowrap" onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        </div>
      </div>
      {scanning && <ScanSpoolModal onClose={() => setScanning(false)} onAssigned={refetchFleet} />}

      {(layout === 'cards' || narrow) && (
        <FleetGrid media={media} printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} printerTypes={printerTypes} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} dueRowsByPrinter={dueRowsByPrinter} refetchMaintenance={refetchMaintenance} fleetModels={fleetModels} />
      )}
      {layout === 'rows' && !narrow && (
        <FleetRows printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} printerTypes={printerTypes} refetchFleet={refetchFleet} snapshotIntervalMs={snapshotIntervalMs} dueRowsByPrinter={dueRowsByPrinter} refetchMaintenance={refetchMaintenance} fleetModels={fleetModels} />
      )}
    </div>
  );
}
