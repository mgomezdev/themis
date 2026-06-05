import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { fmtTime } from '../data/helpers';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import type { ApiPrinter } from '../api/printers';
import { uploadFile, createJob, getFilePlates, getPrinterProfiles, plateThumbnailUrl, checkOverrides, type ApiPlate, type OverrideCheck } from '../api/queue';
import { useFiles, getFiles } from '../api/files';
import { useOrders } from '../api/orders';
import { useSpoolmanConfig, useFilaments, filamentDisplayName } from '../api/spoolman';

// ============================================================
// Types
// ============================================================

interface Plate {
  id: string;
  index: number;
  name: string;
  estTime: number;
  thumbColor: string;
  thumbnailPath: string | null;
  fileId: number | null;
}

interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;  // display name (from Spoolman or null)
  filamentId: number | null;       // Spoolman filament ID when sourced from catalog
  filamentType: string | null;     // material: PLA, PETG, ABS, …
  filamentColor: string | null;    // hex color: #RRGGBB
}

interface PlateConfig {
  selected: boolean;
  jobName: string;
  orderId: number | null;
  selectedPrinters: string[];
  perPrinter: Record<string, PerPrinterCfg>;
}

interface FileInfo {
  name: string;
  size: number;
  type: 'stl' | '3mf';
}

// ============================================================
// Misc helpers
// ============================================================

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

const BADGE: Record<string, string> = {
  elegoo_centauri: 'ECC',
  bambu: 'P1S',
};

// ============================================================
// Printer list hook
// ============================================================

function usePrinterList(): ApiPrinter[] {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  useEffect(() => {
    fetch('/api/v1/printers')
      .then(r => r.json())
      .then(setPrinters)
      .catch(console.error);
  }, []);
  return printers;
}

// ============================================================
// Printer profiles hook — fetches per printer when selected
// ============================================================

function usePrinterProfiles(printerId: number | null): { printProfiles: string[]; filamentProfiles: string[] } {
  const [data, setData] = useState<{ printProfiles: string[]; filamentProfiles: string[] }>({
    printProfiles: [],
    filamentProfiles: [],
  });

  useEffect(() => {
    if (printerId == null) return;
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => {
        if (alive) setData({ printProfiles: p.print_profiles, filamentProfiles: p.filament_profiles });
      })
      .catch(console.error);
    return () => { alive = false; };
  }, [printerId]);

  return data;
}

// ============================================================
// Small sub-components
// ============================================================

function StepNum({ n, done }: { n: number; done: boolean }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 20, height: 20,
      background: done ? 'var(--accent)' : 'var(--bg-3)',
      color: done ? 'white' : 'var(--text-3)',
      border: `1px solid ${done ? 'var(--accent)' : 'var(--border-1)'}`,
      borderRadius: 6,
      fontSize: 11, fontWeight: 600, marginRight: 10,
      fontFamily: 'var(--font-mono)',
      verticalAlign: 'middle',
    }}>
      {done ? React.cloneElement(Icons.check as React.ReactElement<{ size?: number }>, { size: 12 }) : n}
    </span>
  );
}

function Checkbox({ checked, disabled }: { checked: boolean; disabled?: boolean }) {
  return (
    <div style={{
      width: 18, height: 18, flexShrink: 0,
      borderRadius: 5,
      border: `1.5px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
      background: checked ? 'var(--accent)' : 'transparent',
      display: 'grid', placeItems: 'center',
      opacity: disabled ? 0.5 : 1,
    }}>
      {checked && (
        <span style={{ color: 'white', display: 'inline-flex' }}>
          {React.cloneElement(Icons.check as React.ReactElement<{ size?: number; stroke?: number }>, { size: 12, stroke: 3 })}
        </span>
      )}
    </div>
  );
}

function SummaryDot({ done }: { done: boolean }) {
  return (
    <span style={{
      width: 16, height: 16, flexShrink: 0,
      borderRadius: '50%',
      background: done ? 'var(--accent)' : 'var(--bg-3)',
      border: `1px solid ${done ? 'var(--accent)' : 'var(--border-1)'}`,
      display: 'inline-grid', placeItems: 'center', marginTop: 1,
    }}>
      {done && (
        <span style={{ color: 'white', display: 'inline-flex' }}>
          {React.cloneElement(Icons.check as React.ReactElement<{ size?: number; stroke?: number }>, { size: 10, stroke: 3 })}
        </span>
      )}
    </span>
  );
}

function TabDot({ queued, complete }: { queued: boolean; complete: boolean }) {
  if (!queued) {
    return (
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        border: '1px solid var(--border-2)',
        background: 'transparent',
        flexShrink: 0,
      }} />
    );
  }
  const color = complete ? 'var(--ok)' : 'var(--warn)';
  return (
    <span style={{
      width: 8, height: 8, borderRadius: '50%',
      background: color,
      boxShadow: complete ? `0 0 6px ${color}` : 'none',
      flexShrink: 0,
    }} />
  );
}

// ============================================================
// Dropzone
// ============================================================

interface DropzoneProps {
  dragOver: boolean;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onClick: () => void;
}

function Dropzone({ dragOver, onDragEnter, onDragLeave, onDragOver, onDrop, onClick }: DropzoneProps) {
  return (
    <div
      onClick={onClick}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      style={{
        border: `1.5px dashed ${dragOver ? 'var(--accent)' : 'var(--border-2)'}`,
        background: dragOver ? 'var(--accent-glow)' : 'var(--bg-1)',
        borderRadius: 12,
        padding: '32px 24px',
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'background 120ms, border-color 120ms',
      }}>
      <div style={{
        width: 48, height: 48, borderRadius: 12,
        background: 'var(--bg-3)', color: dragOver ? 'var(--accent-hi)' : 'var(--text-3)',
        display: 'grid', placeItems: 'center', margin: '0 auto 12px',
      }}>
        {React.cloneElement(Icons.upload as React.ReactElement<{ size?: number }>, { size: 22 })}
      </div>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-1)' }}>
        Drop a .3mf or .stl file
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        Or click to browse · multi-plate 3MFs supported
      </div>
    </div>
  );
}

// ============================================================
// FileCard
// ============================================================

function FileCard({ file, plateCount, selectedCount, onClear }: {
  file: FileInfo;
  plateCount: number;
  selectedCount: number;
  onClear: () => void;
}) {
  return (
    <div className="row gap-3" style={{
      padding: '14px 16px', background: 'var(--bg-1)',
      border: '1px solid var(--border-1)', borderRadius: 10,
    }}>
      <div style={{
        width: 44, height: 44, borderRadius: 8,
        background: 'linear-gradient(135deg, #1e3a8a, #3b82f6)',
        color: 'white',
        display: 'grid', placeItems: 'center',
        fontWeight: 700, fontSize: 10.5,
        fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
        border: '1px solid var(--border-2)',
      }}>
        {file.type.toUpperCase()}
      </div>
      <div className="col" style={{ flex: 1, minWidth: 0 }}>
        <div className="small" style={{
          fontWeight: 500, color: 'var(--text-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {file.name}
        </div>
        <div className="tiny muted" style={{ marginTop: 2 }}>
          {plateCount === 1
            ? 'Single plate'
            : `${plateCount} plates · ${selectedCount} selected → ${selectedCount} job${selectedCount === 1 ? '' : 's'}`}
          {file.size ? ` · ${formatBytes(file.size)}` : ''}
        </div>
      </div>
      <button className="btn ghost sm" onClick={onClear}>{Icons.x} Replace</button>
    </div>
  );
}

// ============================================================
// QueueToggle
// ============================================================

function QueueToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      role="switch"
      aria-checked={checked}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'transparent', border: 'none',
        padding: '4px 6px', cursor: 'pointer',
        color: 'var(--text-1)', fontFamily: 'inherit',
        flexShrink: 0,
      }}>
      <div className="col" style={{ alignItems: 'flex-end', lineHeight: 1.2 }}>
        <span style={{ fontSize: 12.5, fontWeight: 500, color: checked ? 'var(--text-1)' : 'var(--text-3)' }}>
          {checked ? 'Send to queue' : 'Skip this plate'}
        </span>
        <span className="tiny muted" style={{ marginTop: 2 }}>
          {checked ? 'Creates one job' : 'No job created'}
        </span>
      </div>
      <div style={{
        width: 36, height: 20, borderRadius: 999,
        background: checked ? 'var(--accent)' : 'var(--bg-3)',
        border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
        position: 'relative',
        transition: 'background 120ms, border-color 120ms',
        flexShrink: 0,
        boxShadow: checked ? '0 0 0 3px var(--accent-glow)' : 'none',
      }}>
        <div style={{
          width: 14, height: 14, borderRadius: '50%',
          background: 'white',
          position: 'absolute', top: 2,
          left: checked ? 18 : 2,
          transition: 'left 120ms',
          boxShadow: '0 1px 2px rgba(0,0,0,0.25)',
        }} />
      </div>
    </button>
  );
}

// ============================================================
// OrdersPicker — single-select from real API orders
// ============================================================

function OrdersPicker({ selectedOrderId, onChange }: {
  selectedOrderId: number | null;
  onChange: (id: number | null) => void;
}) {
  const navigate = useNavigate();
  const { orders } = useOrders();
  const open = orders.filter(o => o.status !== 'complete');

  return (
    <div className="col gap-2">
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        {open.map(o => {
          const selected = selectedOrderId === o.id;
          return (
            <button key={o.id} onClick={() => onChange(selected ? null : o.id)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 12px',
                background: selected ? 'var(--bg-3)' : 'var(--bg-1)',
                border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
                boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
                borderRadius: 999, cursor: 'pointer', color: 'var(--text-1)', fontFamily: 'inherit', fontSize: 12,
              }}>
              <span className="mono tiny" style={{ color: 'var(--text-3)' }}>#{o.id}</span>
              <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.customer}</span>
            </button>
          );
        })}
        <button onClick={() => navigate('/orders/new')}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                   background: 'transparent', border: '1px dashed var(--border-2)', borderRadius: 999,
                   color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
          {Icons.plus} New order
        </button>
        {selectedOrderId != null && (
          <button onClick={() => onChange(null)}
            style={{ padding: '6px 10px', background: 'transparent', border: 'none', color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
            None — standalone job
          </button>
        )}
      </div>
    </div>
  );
}

// ============================================================
// PrinterPicker — uses real ApiPrinter list
// ============================================================

function PrinterPicker({ printers, selectedPrinters, onToggle }: {
  printers: ApiPrinter[];
  selectedPrinters: string[];
  onToggle: (id: string) => void;
}) {
  if (printers.length === 0) {
    return (
      <div className="tiny muted" style={{ padding: '12px 0' }}>
        No printers configured. Add a printer in the Printers screen first.
      </div>
    );
  }
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
      gap: 10,
    }}>
      {printers.map(printer => {
        const sid = String(printer.id);
        const selected = selectedPrinters.includes(sid);
        const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();
        return (
          <button
            key={printer.id}
            onClick={() => onToggle(sid)}
            style={{
              padding: 12,
              background: selected ? 'var(--bg-3)' : 'var(--bg-1)',
              border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
              boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
              borderRadius: 10, textAlign: 'left',
              cursor: 'pointer',
              color: 'var(--text-1)', fontFamily: 'inherit',
              position: 'relative',
            }}>
            <div className="row gap-2" style={{ alignItems: 'center' }}>
              <span className={`elig ${selected ? 'on' : 'off'}`}
                    style={selected ? { background: 'rgba(59,130,246,0.20)' } : undefined}>
                {badge}
              </span>
              <div className="col" style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ fontWeight: 500 }}>{printer.name}</div>
                <div className="tiny muted">{printer.printer_type}</div>
              </div>
              <Checkbox checked={selected} />
            </div>
            {printer.current_orca_printer_profile && (
              <div className="tiny" style={{ marginTop: 8, color: 'var(--text-3)' }}>
                {printer.current_orca_printer_profile}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// PerPrinterConfig — uses real OrcaSlicer profiles from API
// ============================================================

function PerPrinterConfig({ printerId, printers, config, onChange }: {
  printerId: string;
  printers: ApiPrinter[];
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
}) {
  const pid = Number(printerId);
  const printer = printers.find(p => p.id === pid);
  const { printProfiles } = usePrinterProfiles(pid);
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const filaments = useFilaments(spoolmanActive);
  const [manualMode, setManualMode] = useState(false);

  // Default color to neutral grey when manual inputs first appear (color picker can't be empty).
  useEffect(() => {
    if ((!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!printer) return null;

  const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();

  return (
    <div style={{
      padding: 14, background: 'var(--bg-1)',
      border: '1px solid var(--border-1)', borderRadius: 10,
    }}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className="elig on">{badge}</span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{printer.name}</div>
          <div className="tiny muted">{printer.printer_type}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="label">Print profile</label>
          <select
            data-testid="print-profile-select"
            className="select"
            value={config.printProfile ?? ''}
            onChange={e => onChange({ printProfile: e.target.value || null })}>
            <option value="">— select profile —</option>
            {printProfiles.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          {printProfiles.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>No profiles found for this printer</div>
          )}
        </div>

        <div>
          <label className="label">Filament</label>
          {spoolmanActive && !manualMode ? (
            <select
              data-testid="filament-catalog-select"
              className="select"
              value={config.filamentProfile ?? ''}
              onChange={e => {
                const v = e.target.value;
                if (v === '__manual__') {
                  setManualMode(true);
                  onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null });
                  return;
                }
                const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
                onChange({
                  filamentProfile: v || null,
                  filamentId: f?.id ?? null,
                  filamentType: f?.material ?? null,
                  filamentColor: f?.color_hex ? `#${f.color_hex}` : null,
                });
              }}>
              <option value="">— select filament —</option>
              {filaments.map(f => (
                <option key={f.id} value={filamentDisplayName(f)}>
                  {filamentDisplayName(f)} · {f.material}
                </option>
              ))}
              <option value="__manual__">Enter manually…</option>
            </select>
          ) : (
            <div className="col gap-2" style={{ marginTop: 2 }}>
              <div className="row gap-2">
                <input
                  data-testid="filament-type-input"
                  className="input"
                  list="filament-types"
                  placeholder="Type (PLA, PETG, ABS…)"
                  value={config.filamentType ?? ''}
                  onChange={e => onChange({ filamentType: e.target.value || null, filamentProfile: e.target.value || null, filamentId: null })}
                  style={{ flex: 1 }}
                />
                {spoolmanActive && (
                  <button className="btn ghost sm" onClick={() => {
                    setManualMode(false);
                    onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null });
                  }}>↩ Catalog</button>
                )}
              </div>
              <datalist id="filament-types">
                {['PLA', 'PLA+', 'PETG', 'ABS', 'ASA', 'TPU', 'Nylon', 'PC'].map(t => (
                  <option key={t} value={t} />
                ))}
              </datalist>
              <div className="row gap-2" style={{ alignItems: 'center' }}>
                <input
                  data-testid="filament-color-input"
                  type="color"
                  value={config.filamentColor ?? '#888888'}
                  onChange={e => onChange({ filamentColor: e.target.value })}
                  style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }}
                />
                <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
              </div>
            </div>
          )}
          {spoolmanActive && !manualMode && filaments.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>No filaments in Spoolman</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// PlateThumbnail — shared image for a plate across all screens
// ============================================================

function PlateThumbnail({
  fileId,
  thumbnailPath,
  label,
  style,
}: {
  fileId: number | null;
  thumbnailPath: string | null;
  label: string;
  style?: React.CSSProperties;
}) {
  const url = fileId != null ? plateThumbnailUrl(fileId, thumbnailPath) : null;
  return (
    <div style={{
      borderRadius: 8, flexShrink: 0,
      background: 'linear-gradient(135deg, #1e3a6e, #3b82f6)',
      border: '1px solid var(--border-2)',
      display: 'grid', placeItems: 'center',
      overflow: 'hidden',
      ...style,
    }}>
      {url ? (
        <img src={url} alt={label}
             style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : (
        <span style={{ color: 'rgba(255,255,255,0.7)', fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
          {label}
        </span>
      )}
    </div>
  );
}

// ============================================================
// PlateConfigPanel
// ============================================================

function PlateConfigPanel({ plate, config, isMultiPlate, printers, onSetField, onTogglePrinter, onSetPerPrinter, onSetOrder, onToggleQueued }: {
  plate: Plate;
  config: PlateConfig;
  isMultiPlate: boolean;
  printers: ApiPrinter[];
  onSetField: (patch: Partial<PlateConfig>) => void;
  onTogglePrinter: (id: string) => void;
  onSetPerPrinter: (printerId: string, patch: Partial<PerPrinterCfg>) => void;
  onSetOrder: (id: number | null) => void;
  onToggleQueued: (v: boolean) => void;
}) {
  const queued = !!config.selected;

  return (
    <div className="col gap-4">
      {/* Plate banner with queue toggle */}
      <div className="row gap-3" style={{ alignItems: 'center' }}>
        <PlateThumbnail
          fileId={plate.fileId}
          thumbnailPath={plate.thumbnailPath}
          label={`P${plate.index}`}
          style={{ width: 56, height: 50, opacity: queued ? 1 : 0.55 }}
        />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: queued ? 'var(--text-1)' : 'var(--text-3)' }}>
            {plate.name}
          </div>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            est. {fmtTime(plate.estTime)}
          </div>
        </div>
        {isMultiPlate && <QueueToggle checked={queued} onChange={onToggleQueued} />}
      </div>

      {/* Body: dims when plate is skipped */}
      <div
        style={{
          opacity: queued ? 1 : 0.5,
          pointerEvents: queued ? 'auto' : 'none',
          transition: 'opacity 120ms',
        }}
        aria-disabled={!queued}>
        <div className="col gap-4">
          {/* Eligible printers */}
          <div>
            <div className="row between" style={{ marginBottom: 10, alignItems: 'baseline' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
                  <StepNum n={2} done={config.selectedPrinters.length > 0} />
                  Eligible printers
                </div>
                <div className="tiny muted" style={{ marginTop: 2, marginLeft: 30 }}>
                  Choose which printers may claim this plate. Configure preset + filament for each.
                </div>
              </div>
            </div>
            <PrinterPicker
              printers={printers}
              selectedPrinters={config.selectedPrinters}
              onToggle={onTogglePrinter}
            />
            {config.selectedPrinters.length > 0 && (
              <div className="col gap-3" style={{ marginTop: 14 }}>
                {config.selectedPrinters.map(pid => (
                  <PerPrinterConfig
                    key={pid}
                    printerId={pid}
                    printers={printers}
                    config={config.perPrinter[pid] ?? { printProfile: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null }}
                    onChange={patch => onSetPerPrinter(pid, patch)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Orders */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
              <StepNum n={3} done={config.orderId != null} />
              Fulfills order
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              Link this plate to the customer or internal order its parts ship into. Optional.
            </div>
            <OrdersPicker selectedOrderId={config.orderId} onChange={onSetOrder} />
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Job details */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
              <StepNum n={4} done={!!config.jobName.trim()} />
              Job details
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              How this plate-job appears in the queue.
            </div>
            <div>
              <label className="label">Job name</label>
              <input
                className="input"
                value={config.jobName}
                onChange={e => onSetField({ jobName: e.target.value })}
                placeholder="e.g. PA-CF arm brackets"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SummaryCard
// ============================================================

function SummaryCard({ file, plates, plateConfigs, selectedPlateIds, activePlateId, plateIsComplete }: {
  file: FileInfo | null;
  plates: Plate[];
  plateConfigs: Record<string, PlateConfig>;
  selectedPlateIds: string[];
  activePlateId: string | null;
  plateIsComplete: (id: string) => boolean;
}) {
  const totalTime = selectedPlateIds.reduce((a, id) => {
    const p = plates.find(x => x.id === id);
    return a + (p?.estTime ?? 0);
  }, 0);

  const allOrders = new Set<number>();
  selectedPlateIds.forEach(id => {
    const oid = plateConfigs[id]?.orderId;
    if (oid != null) allOrders.add(oid);
  });

  return (
    <div className="card" style={{ padding: 18 }}>
      <div className="tag-key">Job summary</div>

      <div className="row gap-2" style={{ marginTop: 12 }}>
        <SummaryDot done={!!file} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{
            color: file ? 'var(--text-1)' : 'var(--text-3)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {file ? file.name : 'No file yet'}
          </div>
        </div>
      </div>

      <div className="row gap-2" style={{ marginTop: 10, alignItems: 'flex-start' }}>
        <SummaryDot done={selectedPlateIds.length > 0} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{ color: selectedPlateIds.length ? 'var(--text-1)' : 'var(--text-3)' }}>
            {selectedPlateIds.length === 0
              ? 'No plates picked'
              : `${selectedPlateIds.length} plate${selectedPlateIds.length === 1 ? '' : 's'} → ${selectedPlateIds.length} job${selectedPlateIds.length === 1 ? '' : 's'}`}
          </div>
        </div>
      </div>

      {selectedPlateIds.length > 0 && (
        <div className="col gap-1" style={{ marginTop: 8, marginLeft: 24 }}>
          {selectedPlateIds.map(id => {
            const cfg = plateConfigs[id];
            const plate = plates.find(p => p.id === id);
            const complete = plateIsComplete(id);
            const isActive = activePlateId === id;
            return (
              <div key={id} className="row gap-2" style={{ alignItems: 'center' }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                  background: complete ? 'var(--ok)' : 'var(--warn)',
                  boxShadow: complete ? '0 0 4px var(--ok)' : 'none',
                }} />
                <span className="tiny" style={{
                  color: isActive ? 'var(--text-1)' : 'var(--text-2)',
                  fontWeight: isActive ? 500 : 400,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  flex: 1, minWidth: 0,
                }}>
                  P{plate?.index} · {cfg.jobName}
                </span>
                <span className="num tiny muted" style={{ flexShrink: 0 }}>
                  {cfg.selectedPrinters.length ?? 0}p
                </span>
              </div>
            );
          })}
        </div>
      )}

      {allOrders.size > 0 && (
        <>
          <div className="divider" />
          <div className="tag-key">Fulfills</div>
          <div className="row gap-1" style={{ marginTop: 6, flexWrap: 'wrap' }}>
            {Array.from(allOrders).map(id => (
              <span key={id} className="mono tiny" style={{ padding: '2px 8px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 999, color: 'var(--text-2)' }}>#{id}</span>
            ))}
          </div>
        </>
      )}

      <div className="divider" />

      <div className="row between">
        <span className="tag-key">Total plate time</span>
        <span className="num small">{totalTime > 0 ? fmtTime(totalTime) : '—'}</span>
      </div>
      <div className="row between" style={{ marginTop: 6 }}>
        <span className="tag-key">Slicing</span>
        <span className="small" style={{ color: 'var(--text-2)' }}>On claim</span>
      </div>
    </div>
  );
}

// ============================================================
// Main screen
// ============================================================

function platesToLocal(apiPlates: ApiPlate[], fileId: number): Plate[] {
  return apiPlates.map(p => ({
    id: `plate-${p.plate_number}`,
    index: p.plate_number,
    name: `Plate ${p.plate_number}`,
    estTime: p.estimated_time,
    thumbColor: '#2a3552',
    thumbnailPath: p.thumbnail_path,
    fileId,
  }));
}

function defaultConfigForPlate(plate: Plate): PlateConfig {
  return {
    selected: true,
    jobName: plate.name,
    orderId: null,
    selectedPrinters: [],
    perPrinter: {},
  };
}

interface MergedFindings {
  changes: { key: string; from: string; to: string }[];
  slotWarning: { used_slots: number; printer_slots: number } | null;
}

function mergeFindings(checks: OverrideCheck[]): MergedFindings {
  const seen = new Set<string>();
  const changes: MergedFindings['changes'] = [];
  let slotWarning: MergedFindings['slotWarning'] = null;
  for (const c of checks) {
    for (const ch of c.setting_changes) {
      const k = `${ch.key}|${ch.from}|${ch.to}`;
      if (!seen.has(k)) { seen.add(k); changes.push(ch); }
    }
    if (c.slot_warning) slotWarning = c.slot_warning;
  }
  return { changes, slotWarning };
}

function OverrideAlertModal({ findings, onProceed, onCancel }: {
  findings: MergedFindings;
  onProceed: () => void;
  onCancel: () => void;
}) {
  return (
    <div onClick={onCancel} style={{
      position: 'fixed', inset: 0, background: 'rgba(2,6,16,0.65)', backdropFilter: 'blur(4px)',
      zIndex: 200, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ width: 'min(560px,100%)', padding: 24 }}>
        <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 6 }}>
          <span style={{ color: 'var(--warn)' }}>{Icons.alert}</span>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>This file has settings that won't carry over</h2>
        </div>
        <div className="muted small" style={{ marginBottom: 14, lineHeight: 1.5 }}>
          Slicing applies the printer and presets you selected, replacing these settings baked into the uploaded file.
          Per-object overrides, modifiers, and paint are kept.
        </div>
        {findings.slotWarning && (
          <div style={{ padding: '10px 12px', borderRadius: 8, marginBottom: 12,
                        background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>
            File uses <strong>{findings.slotWarning.used_slots}</strong> filament slots, but the printer has{' '}
            <strong>{findings.slotWarning.printer_slots}</strong>. Some parts may not map to a loaded filament.
          </div>
        )}
        {findings.changes.length > 0 && (
          <div style={{ border: '1px solid var(--border-1)', borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
            {findings.changes.map((c, i) => (
              <div key={c.key} className="row between" style={{
                padding: '8px 12px', fontSize: 13,
                borderTop: i ? '1px solid var(--border-1)' : 'none', background: i % 2 ? 'var(--bg-1)' : 'transparent',
              }}>
                <span className="mono" style={{ color: 'var(--text-2)' }}>{c.key}</span>
                <span><span style={{ color: 'var(--text-3)' }}>{c.from}</span> → <span style={{ color: 'var(--text-1)', fontWeight: 500 }}>{c.to}</span></span>
              </div>
            ))}
          </div>
        )}
        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onCancel}>Change preset/printer</button>
          <button className="btn primary" onClick={onProceed}>Proceed, accept changes</button>
        </div>
      </div>
    </div>
  );
}

export function NewJobScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const printers = usePrinterList();

  const [file, setFile] = useState<FileInfo | null>(null);
  const [uploadedFileId, setUploadedFileId] = useState<number | null>(null);
  const [plates, setPlates] = useState<Plate[]>([]);
  const [plateConfigs, setPlateConfigs] = useState<Record<string, PlateConfig>>({});
  const [activePlateId, setActivePlateId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [overrideFindings, setOverrideFindings] = useState<MergedFindings | null>(null);

  // Source selection: upload a new file or pick one from the library.
  const [source, setSource] = useState<'upload' | 'library'>('upload');
  const { files: libraryFiles } = useFiles({});
  const [saveFolder, setSaveFolder] = useState('/Job Uploads');

  useEffect(() => {
    if (!successMsg) return;
    const t = setTimeout(() => setSuccessMsg(null), 4000);
    return () => clearTimeout(t);
  }, [successMsg]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectedPlateIds = useMemo(
    () => plates.filter(p => plateConfigs[p.id]?.selected).map(p => p.id),
    [plates, plateConfigs],
  );

  useEffect(() => {
    if (plates.length > 0 && !plates.find(p => p.id === activePlateId)) {
      setActivePlateId(plates[0].id);
    }
  }, [plates, activePlateId]);

  // ---- file actions ----

  // Shared path for both upload and library-pick: the /upload response no longer
  // carries a `plates` array, so plates are always loaded via getFilePlates(id).
  async function loadFileIntoState(fileId: number, fileInfo: FileInfo) {
    setUploadedFileId(fileId);
    setFile(fileInfo);
    const apiPlates = await getFilePlates(fileId);
    const detected = platesToLocal(apiPlates, fileId);
    setPlates(detected);
    const configs: Record<string, PlateConfig> = {};
    detected.forEach(p => { configs[p.id] = defaultConfigForPlate(p); });
    setPlateConfigs(configs);
    setActivePlateId(detected[0]?.id ?? null);
  }

  function fileTypeOf(name: string): 'stl' | '3mf' {
    return name.toLowerCase().endsWith('.stl') ? 'stl' : '3mf';
  }

  async function handleFile(rawFile: File | null | undefined) {
    if (!rawFile) return;
    const nameLower = rawFile.name.toLowerCase();
    if (!nameLower.endsWith('.3mf') && !nameLower.endsWith('.stl')) {
      setError('Only .3mf and .stl files are supported.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const uploaded = await uploadFile(rawFile, saveFolder || undefined);
      await loadFileIntoState(uploaded.id, {
        name: uploaded.original_filename,
        size: rawFile.size,
        type: fileTypeOf(uploaded.original_filename),
      });
    } catch (err) {
      setError(`Upload failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  }

  async function selectLibraryFile(fileId: number) {
    setUploading(true);
    setError(null);
    try {
      // Prefer the already-loaded library list; fall back to a fetch if navigated
      // here directly (e.g. from FilesScreen) before the list resolves.
      let lib = libraryFiles.find(f => f.id === fileId);
      if (!lib) {
        const all = await getFiles({});
        lib = all.find(f => f.id === fileId);
      }
      if (!lib) {
        setError('That library file could not be found.');
        return;
      }
      await loadFileIntoState(lib.id, {
        name: lib.original_filename,
        size: lib.size_bytes,
        type: fileTypeOf(lib.original_filename),
      });
    } catch (err) {
      setError(`Failed to load file: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  }

  // Preselect a library file when navigated here from the Files screen
  // ("Use in new job" → state.libraryFileId).
  useEffect(() => {
    const id = (location.state as { libraryFileId?: number } | null)?.libraryFileId;
    if (id) { setSource('library'); selectLibraryFile(id); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  function clearFile() {
    setFile(null); setUploadedFileId(null); setPlates([]);
    setPlateConfigs({}); setActivePlateId(null); setError(null);
  }

  // ---- plate config mutators ----
  function setPlateConfig(plateId: string, patch: Partial<PlateConfig>) {
    setPlateConfigs(prev => ({ ...prev, [plateId]: { ...prev[plateId], ...patch } }));
  }

  function togglePlate(plateId: string, value: boolean) {
    setPlateConfig(plateId, { selected: value });
  }

  function togglePrinterForPlate(plateId: string, printerId: string) {
    setPlateConfigs(prev => {
      const cfg = prev[plateId];
      if (!cfg) return prev;
      const inSelection = cfg.selectedPrinters.includes(printerId);
      if (inSelection) {
        const nextPP = { ...cfg.perPrinter };
        delete nextPP[printerId];
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: cfg.selectedPrinters.filter(id => id !== printerId),
            perPrinter: nextPP,
          },
        };
      } else {
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: [...cfg.selectedPrinters, printerId],
            perPrinter: {
              ...cfg.perPrinter,
              [printerId]: { printProfile: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null },
            },
          },
        };
      }
    });
  }

  function setPerPrinter(plateId: string, printerId: string, patch: Partial<PerPrinterCfg>) {
    setPlateConfigs(prev => ({
      ...prev,
      [plateId]: {
        ...prev[plateId],
        perPrinter: {
          ...prev[plateId].perPrinter,
          [printerId]: { ...prev[plateId].perPrinter[printerId], ...patch },
        },
      },
    }));
  }

  function setOrderForPlate(plateId: string, orderId: number | null) {
    setPlateConfig(plateId, { orderId });
  }

  // ---- validation ----
  const plateIsComplete = (plateId: string): boolean => {
    const cfg = plateConfigs[plateId];
    if (!cfg || !cfg.selected) return false;
    if (!cfg.jobName?.trim()) return false;
    if (cfg.selectedPrinters.length === 0) return false;
    return cfg.selectedPrinters.every(pid => {
      const pp = cfg.perPrinter[pid];
      return !!(pp && pp.printProfile && pp.filamentType && pp.filamentColor);
    });
  };

  const isComplete = uploadedFileId != null && selectedPlateIds.length > 0 && selectedPlateIds.every(plateIsComplete);

  // Override-loss check: warn before slicing replaces settings baked into the 3MF.
  async function handleCreate() {
    if (!uploadedFileId || !isComplete) return;
    setSubmitting(true);
    setError(null);
    try {
      const checks = await Promise.all(
        selectedPlateIds.flatMap(id => {
          const cfg = plateConfigs[id];
          return cfg.selectedPrinters.map(pid =>
            checkOverrides({
              uploaded_file_id: uploadedFileId,
              printer_id: Number(pid),
              print_profile: cfg.perPrinter[pid].printProfile!,
              filament_profile: cfg.perPrinter[pid].filamentProfile,
              filament_color: cfg.perPrinter[pid].filamentColor,
            }).catch(() => null),
          );
        }),
      );
      const withFindings = checks.filter((c): c is OverrideCheck => !!c && c.has_findings);
      if (withFindings.length > 0) {
        setOverrideFindings(mergeFindings(withFindings));
        setSubmitting(false);
        return;  // wait for the user's decision in the modal
      }
    } catch {
      // Non-fatal: if the check itself errors, fall through to creation.
    }
    await doCreate();
  }

  async function doCreate() {
    if (!uploadedFileId) return;
    setSubmitting(true);
    setError(null);
    setOverrideFindings(null);
    const count = selectedPlateIds.length;
    try {
      for (const id of selectedPlateIds) {
        const plate = plates.find(p => p.id === id)!;
        const cfg = plateConfigs[id];
        await createJob({
          uploaded_file_id: uploadedFileId,
          plate_number: plate.index,
          order_id: cfg.orderId,
          printer_configs: cfg.selectedPrinters.map(pid => ({
            printer_id: Number(pid),
            print_profile: cfg.perPrinter[pid].printProfile!,
            filament_profile: cfg.perPrinter[pid].filamentProfile ?? null,
            filament_id: cfg.perPrinter[pid].filamentId ?? null,
            filament_type: cfg.perPrinter[pid].filamentType,
            filament_color: cfg.perPrinter[pid].filamentColor,
          })),
        });
      }
      clearFile();
      setSuccessMsg(`${count} job${count === 1 ? '' : 's'} added to queue`);
    } catch (err) {
      setError(`Failed to create job: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="col gap-4">
      {overrideFindings && (
        <OverrideAlertModal
          findings={overrideFindings}
          onProceed={doCreate}
          onCancel={() => setOverrideFindings(null)}
        />
      )}
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate('/queue')}>{Icons.chevL} Queue</button>
        <span className="muted small">/</span>
        <span className="small">New job</span>
      </div>

      {successMsg && (
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)',
          color: 'var(--ok)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          {Icons.check} {successMsg} — <button className="btn ghost sm" style={{ padding: '0 4px', color: 'inherit' }} onClick={() => navigate('/queue')}>view queue</button>
        </div>
      )}
      {error && (
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)',
          color: 'var(--err)', fontSize: 13,
        }}>
          {error}
        </div>
      )}

      <div className="layout-main-sidebar">
        <div className="col gap-4">

          {/* Step 1: source file */}
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader
              title={<span><StepNum n={1} done={!!file} /> Source file</span>}
              sub="Upload a new .3mf/.stl or pick one from your library."
            />

            {!file && !uploading && (
              <div className="row gap-2" style={{ marginBottom: 14 }}>
                <button
                  className={`btn sm ${source === 'upload' ? 'primary' : 'ghost'}`}
                  onClick={() => setSource('upload')}>
                  Upload
                </button>
                <button
                  className={`btn sm ${source === 'library' ? 'primary' : 'ghost'}`}
                  onClick={() => setSource('library')}>
                  Pick from library
                </button>
              </div>
            )}

            {uploading ? (
              <div className="tiny muted" style={{ padding: '24px 0', textAlign: 'center' }}>
                Loading and parsing plates…
              </div>
            ) : file ? (
              <FileCard
                file={file}
                plateCount={plates.length}
                selectedCount={selectedPlateIds.length}
                onClear={clearFile}
              />
            ) : source === 'library' ? (
              <div className="col gap-2">
                {libraryFiles.length === 0 ? (
                  <div className="tiny muted" style={{ padding: '12px 0' }}>
                    No files in the library yet. Upload one or add files in the Files screen.
                  </div>
                ) : (
                  libraryFiles.map(f => (
                    <button
                      key={f.id}
                      className="btn ghost"
                      style={{ justifyContent: 'space-between', width: '100%' }}
                      onClick={() => selectLibraryFile(f.id)}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.original_filename}
                      </span>
                      <span className="tiny muted" style={{ marginLeft: 12, flexShrink: 0 }}>{f.folder}</span>
                    </button>
                  ))
                )}
              </div>
            ) : (
              <div className="col gap-3">
                <Dropzone
                  dragOver={dragOver}
                  onDragEnter={() => setDragOver(true)}
                  onDragLeave={() => setDragOver(false)}
                  onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                />
                <div>
                  <label className="label">Save uploaded file to</label>
                  <input
                    className="input"
                    value={saveFolder}
                    onChange={e => setSaveFolder(e.target.value)}
                    placeholder="/Job Uploads"
                  />
                </div>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".3mf,.stl"
              style={{ display: 'none' }}
              onChange={e => handleFile(e.target.files?.[0])}
            />
          </div>

          {/* Step 2: per-plate config */}
          {plates.length > 0 && activePlateId && (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              {plates.length > 1 && (
                <div style={{
                  display: 'flex',
                  borderBottom: '1px solid var(--border-1)',
                  overflow: 'auto',
                }}>
                  {plates.map(plate => {
                    const cfg = plateConfigs[plate.id];
                    const isActive = activePlateId === plate.id;
                    const queued = !!cfg?.selected;
                    const complete = plateIsComplete(plate.id);
                    return (
                      <button
                        key={plate.id}
                        onClick={() => setActivePlateId(plate.id)}
                        style={{
                          padding: '12px 16px',
                          background: isActive ? 'var(--bg-2)' : 'transparent',
                          border: 'none',
                          borderBottom: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
                          color: isActive ? 'var(--text-1)' : queued ? 'var(--text-2)' : 'var(--text-4)',
                          fontFamily: 'inherit',
                          fontSize: 13,
                          fontWeight: isActive ? 500 : 400,
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          whiteSpace: 'nowrap',
                          flexShrink: 0,
                        }}>
                        <TabDot queued={queued} complete={complete} />
                        Plate {plate.index} · {cfg.jobName || plate.name}
                        {!queued && (
                          <span className="mono tiny" style={{
                            padding: '1px 6px', marginLeft: 4,
                            border: '1px solid var(--border-1)',
                            borderRadius: 4, color: 'var(--text-4)',
                            fontSize: 9.5, letterSpacing: '0.04em',
                          }}>
                            SKIP
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              <div style={{ padding: 20 }}>
                <PlateConfigPanel
                  plate={plates.find(p => p.id === activePlateId)!}
                  config={plateConfigs[activePlateId]}
                  isMultiPlate={plates.length > 1}
                  printers={printers}
                  onSetField={patch => setPlateConfig(activePlateId, patch)}
                  onTogglePrinter={pid => togglePrinterForPlate(activePlateId, pid)}
                  onSetPerPrinter={(pid, patch) => setPerPrinter(activePlateId, pid, patch)}
                  onSetOrder={oid => setOrderForPlate(activePlateId, oid)}
                  onToggleQueued={v => togglePlate(activePlateId, v)}
                />
              </div>
            </div>
          )}
        </div>

        {/* Right rail: summary */}
        <div className="col gap-4">
          <SummaryCard
            file={file}
            plates={plates}
            plateConfigs={plateConfigs}
            selectedPlateIds={selectedPlateIds}
            activePlateId={activePlateId}
            plateIsComplete={plateIsComplete}
          />
          <div className="card" style={{ padding: 18 }}>
            <button
              className="btn primary"
              style={{ width: '100%' }}
              disabled={!isComplete || submitting}
              onClick={handleCreate}>
              {submitting
                ? 'Adding to queue…'
                : <>{Icons.check} Add {selectedPlateIds.length || ''} job{selectedPlateIds.length === 1 ? '' : 's'} to queue</>
              }
            </button>
            <button
              className="btn ghost sm"
              style={{ width: '100%', marginTop: 8 }}
              disabled={submitting}
              onClick={() => navigate('/queue')}>
              Cancel
            </button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: 'center', lineHeight: 1.5 }}>
              Each plate becomes its own job.<br />Slicing runs when a printer claims.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
