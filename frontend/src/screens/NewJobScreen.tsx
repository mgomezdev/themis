import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { PRINTERS, PROCESS_PRESETS, FILAMENTS, ORDERS, getPrinter } from '../data/mock';
import { fmtTime, darken } from '../data/helpers';
import { Icons } from '../components/icons';
import { StatusPill, SectionHeader } from '../components/ui';
import type { Order, Filament, ProcessPreset } from '../data/types';

// ============================================================
// Types
// ============================================================

interface PlatePart {
  name: string;
  qty: number;
  material: string;
}

interface Plate {
  id: string;
  index: number;
  name: string;
  parts: PlatePart[];
  estTime: number;
  materials: string[];
  thumbColor: string;
  suggestedOrders?: string[];
}

interface PerPrinterCfg {
  processId: string | null;
  filamentId: string | null;
  profileIdx: number;
}

interface PlateConfig {
  selected: boolean;
  jobName: string;
  priority: string;
  orderIds: string[];
  selectedPrinters: string[];
  perPrinter: Record<string, PerPrinterCfg>;
}

interface FileInfo {
  name: string;
  size: number;
  type: 'stl' | '3mf';
}

// ============================================================
// Mocked plate parser — ported verbatim from design
// ============================================================

function readPlatesFromFilename(fileName: string): Plate[] {
  const lower = fileName.toLowerCase();
  if (lower.endsWith('.stl')) {
    return [{
      id: 'plate-1', index: 1, name: 'Single body',
      parts: [{ name: fileName.replace(/\.stl$/i, ''), qty: 1, material: '—' }],
      estTime: 64, materials: ['—'], thumbColor: '#2a3552',
    }];
  }
  if (lower.includes('vr_arm') || lower.includes('arm_bracket')) {
    return [
      { id: 'plate-1', index: 1, name: 'Arm bracket — L (×2)',
        parts: [{ name: 'Arm bracket — L', qty: 2, material: 'PA-CF' }],
        estTime: 156, materials: ['PA-CF'], thumbColor: '#16203a',
        suggestedOrders: ['ORD-2241'] },
      { id: 'plate-2', index: 2, name: 'Arm bracket — R (×2)',
        parts: [{ name: 'Arm bracket — R', qty: 2, material: 'PA-CF' }],
        estTime: 156, materials: ['PA-CF'], thumbColor: '#16203a',
        suggestedOrders: ['ORD-2241'] },
      { id: 'plate-3', index: 3, name: 'Cable clamps (×8)',
        parts: [{ name: 'Cable clamp', qty: 8, material: 'PETG' }],
        estTime: 96, materials: ['PETG'], thumbColor: '#1e3a5a',
        suggestedOrders: ['ORD-2241'] },
    ];
  }
  if (lower.includes('hartwell') || lower.includes('fig')) {
    return [
      { id: 'plate-1', index: 1, name: 'Figure A — multi-color (×4)',
        parts: [{ name: 'Figure A', qty: 4, material: 'PLA (4-color)' }],
        estTime: 152, materials: ['PLA (4-color)'], thumbColor: '#3a2a3a',
        suggestedOrders: ['ORD-2243'] },
      { id: 'plate-2', index: 2, name: 'Figure A — accents (×4)',
        parts: [{ name: 'Figure A accents', qty: 4, material: 'PLA Silk' }],
        estTime: 88, materials: ['PLA (4-color)'], thumbColor: '#3a3a2a',
        suggestedOrders: ['ORD-2243'] },
    ];
  }
  if (lower.includes('cradle') || lower.includes('northbeam')) {
    return [
      { id: 'plate-1', index: 1, name: 'Cradle body (×2)',
        parts: [{ name: 'Cradle body', qty: 2, material: 'PETG' }],
        estTime: 128, materials: ['PETG'], thumbColor: '#1e3a4a',
        suggestedOrders: ['ORD-2244'] },
      { id: 'plate-2', index: 2, name: 'Foot dampener (×4)',
        parts: [{ name: 'Foot dampener', qty: 4, material: 'TPU' }],
        estTime: 72, materials: ['TPU'], thumbColor: '#4a1a1a',
        suggestedOrders: ['ORD-2244'] },
    ];
  }
  if (lower.includes('reflow') || lower.includes('wall_panel')) {
    return [
      { id: 'plate-1', index: 1, name: 'Wall panel (×1)',
        parts: [{ name: 'Wall panel', qty: 1, material: 'ABS' }],
        estTime: 192, materials: ['ABS'], thumbColor: '#222b41',
        suggestedOrders: ['ORD-2242'] },
    ];
  }
  return [
    { id: 'plate-1', index: 1, name: 'Plate 1',
      parts: [{ name: 'Main body', qty: 1, material: 'PLA' }],
      estTime: 84, materials: ['PLA'], thumbColor: '#2a3552' },
    { id: 'plate-2', index: 2, name: 'Plate 2',
      parts: [{ name: 'Lid', qty: 1, material: 'PLA' }],
      estTime: 52, materials: ['PLA'], thumbColor: '#2a3552' },
  ];
}

// ============================================================
// Material → printer compatibility — ported verbatim from design
// ============================================================

function eligibleForMaterial(materials: string[]): string[] {
  const set = new Set<string>();
  for (const m of materials) {
    const lower = (m || '').toLowerCase();
    if (lower.includes('pa-cf') || lower.includes('pc ') || lower === 'pc' ||
        lower.includes('abs') || lower.includes('asa')) {
      set.add('ecc-01'); continue;
    }
    if (lower.includes('4-color') || lower.includes('multi')) {
      set.add('snp-01'); continue;
    }
    if (lower.includes('tpu')) {
      ['p1s-01', 'snp-01'].forEach(id => set.add(id)); continue;
    }
    ['p1s-01', 'ecc-01', 'snp-01'].forEach(id => set.add(id));
  }
  return Array.from(set);
}

// ============================================================
// Filament helpers
// ============================================================

interface FilamentOption {
  filament: Filament;
  profiles: Filament['profiles'];
}

function filamentsForPrinter(printerId: string, plateMaterials: string[]): FilamentOption[] {
  const wantsCF    = plateMaterials.some(m => /cf/i.test(m));
  const wantsTPU   = plateMaterials.some(m => /tpu/i.test(m));
  const wantsABS   = plateMaterials.some(m => /\babs\b/i.test(m));
  const wantsASA   = plateMaterials.some(m => /\basa\b/i.test(m));
  const wantsPETG  = plateMaterials.some(m => /petg/i.test(m));
  const wantsPC    = plateMaterials.some(m => /\bpc\b/i.test(m));
  const wantsMulti = plateMaterials.some(m => /multi|4-color/i.test(m));

  const matchesPlate = (fil: Filament) => {
    const t = fil.type.toLowerCase();
    if (wantsCF)    return t === 'pa-cf' || /cf/i.test(fil.subtype ?? '');
    if (wantsTPU)   return t === 'tpu';
    if (wantsABS)   return t === 'abs';
    if (wantsASA)   return t === 'asa';
    if (wantsPC)    return t === 'pc';
    if (wantsMulti) return t === 'pla';
    if (wantsPETG)  return t === 'petg';
    return t === 'pla';
  };

  return FILAMENTS
    .map(fil => ({
      filament: fil,
      profiles: (fil.profiles ?? []).filter(pf => pf.printerId === printerId),
    }))
    .filter(({ filament, profiles }) => profiles.length > 0 && matchesPlate(filament));
}

function pickFilamentForPrinter(printerId: string, plateMaterials: string[]): { id: string; profileIdx: number } | null {
  const opts = filamentsForPrinter(printerId, plateMaterials);
  if (opts.length === 0) {
    const fallback = FILAMENTS.find(f => (f.profiles ?? []).some(p => p.printerId === printerId));
    if (!fallback) return null;
    return { id: fallback.id, profileIdx: 0 };
  }
  const fav = opts.find(o => o.filament.favorite);
  const pick = fav ?? opts[0];
  return { id: pick.filament.id, profileIdx: 0 };
}

// ============================================================
// Default config for a plate
// ============================================================

function defaultConfigForPlate(plate: Plate): PlateConfig {
  return {
    selected: true,
    jobName: plate.name,
    priority: 'normal',
    orderIds: plate.suggestedOrders ? [...plate.suggestedOrders] : [],
    selectedPrinters: [],
    perPrinter: {},
  };
}

// ============================================================
// Misc helpers
// ============================================================

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
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

function FilamentDot({ color }: { color: string }) {
  return (
    <span style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      background: color, border: '1px solid rgba(255,255,255,0.15)',
      boxShadow: `0 0 0 1px var(--border-2), 0 0 6px ${color}66`,
      flexShrink: 0,
    }} />
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
        background: file.type === '3mf'
          ? 'linear-gradient(135deg, #1e3a8a, #3b82f6)'
          : 'linear-gradient(135deg, #475569, #94a3b8)',
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
// OrdersPicker
// ============================================================

function OrdersPicker({ selectedOrderIds, onChange }: {
  selectedOrderIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const open = ORDERS.filter(o => o.status !== 'complete');
  const complete = ORDERS.filter(o => o.status === 'complete');

  function toggle(id: string) {
    if (selectedOrderIds.includes(id)) {
      onChange(selectedOrderIds.filter(x => x !== id));
    } else {
      onChange([...selectedOrderIds, id]);
    }
  }

  function Chip({ order }: { order: Order }) {
    const selected = selectedOrderIds.includes(order.id);
    return (
      <button
        onClick={(e) => { e.stopPropagation(); toggle(order.id); }}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 8,
          padding: '6px 10px 6px 8px',
          background: selected ? 'var(--bg-3)' : 'var(--bg-1)',
          border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
          boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
          borderRadius: 999,
          cursor: 'pointer', color: 'var(--text-1)',
          fontFamily: 'inherit', fontSize: 12,
          whiteSpace: 'nowrap',
          maxWidth: 280, minWidth: 0,
          transition: 'background 120ms, border-color 120ms',
        }}>
        <span style={{
          width: 14, height: 14, flexShrink: 0,
          borderRadius: 4,
          border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--border-2)'}`,
          background: selected ? 'var(--accent)' : 'transparent',
          display: 'inline-grid', placeItems: 'center',
        }}>
          {selected && (
            <span style={{ color: 'white', display: 'inline-flex' }}>
              {React.cloneElement(Icons.check as React.ReactElement<{ size?: number; stroke?: number }>, { size: 9, stroke: 3 })}
            </span>
          )}
        </span>
        <span className="mono tiny" style={{ color: 'var(--text-3)', flexShrink: 0 }}>{order.id}</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, flex: 1 }}>
          {order.customer}
        </span>
        <StatusPill status={order.status} />
      </button>
    );
  }

  return (
    <div className="col gap-2">
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        {open.map(o => <Chip key={o.id} order={o} />)}
        <button
          onClick={() => { /* placeholder */ }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '6px 10px 6px 8px',
            background: 'transparent',
            border: '1px dashed var(--border-2)',
            borderRadius: 999,
            color: 'var(--text-3)',
            cursor: 'pointer', fontFamily: 'inherit', fontSize: 12,
          }}>
          {React.cloneElement(Icons.plus as React.ReactElement<{ size?: number }>, { size: 12 })} New order
        </button>
        <button
          onClick={() => onChange([])}
          disabled={selectedOrderIds.length === 0}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '6px 10px',
            background: 'transparent', border: 'none',
            color: selectedOrderIds.length === 0 ? 'var(--text-4)' : 'var(--text-3)',
            cursor: selectedOrderIds.length === 0 ? 'default' : 'pointer',
            fontFamily: 'inherit', fontSize: 12,
          }}>
          None — standalone job
        </button>
      </div>
      {complete.length > 0 && (
        <details>
          <summary className="tiny muted" style={{ cursor: 'pointer', padding: '4px 0', userSelect: 'none' }}>
            Show completed orders ({complete.length})
          </summary>
          <div className="row gap-2" style={{ flexWrap: 'wrap', marginTop: 6, opacity: 0.7 }}>
            {complete.map(o => <Chip key={o.id} order={o} />)}
          </div>
        </details>
      )}
    </div>
  );
}

// ============================================================
// PrinterPicker
// ============================================================

function PrinterPicker({ eligibleIds, selectedPrinters, onToggle, materials }: {
  eligibleIds: string[];
  selectedPrinters: string[];
  onToggle: (id: string) => void;
  materials: string[];
}) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
      gap: 10,
    }}>
      {PRINTERS.map(printer => {
        const eligible = eligibleIds.includes(printer.id);
        const selected = selectedPrinters.includes(printer.id);
        return (
          <button
            key={printer.id}
            disabled={!eligible}
            onClick={() => onToggle(printer.id)}
            style={{
              padding: 12,
              background: selected ? 'var(--bg-3)' : 'var(--bg-1)',
              border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
              boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
              borderRadius: 10, textAlign: 'left',
              cursor: eligible ? 'pointer' : 'not-allowed',
              opacity: eligible ? 1 : 0.5,
              color: 'var(--text-1)', fontFamily: 'inherit',
              position: 'relative',
            }}>
            <div className="row gap-2" style={{ alignItems: 'center' }}>
              <span className={`elig ${selected ? 'on' : 'off'}`}
                    style={selected ? { background: 'rgba(59,130,246,0.20)' } : undefined}>
                {printer.badge}
              </span>
              <div className="col" style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ fontWeight: 500 }}>{printer.nickname}</div>
                <div className="tiny muted">{printer.name}</div>
              </div>
              <Checkbox checked={selected} disabled={!eligible} />
            </div>
            <div className="tiny" style={{
              marginTop: 8,
              color: eligible ? 'var(--text-3)' : 'var(--err)',
            }}>
              {eligible
                ? printer.capabilities.slice(0, 3).join(' · ')
                : `Can't print ${materials.join(', ')}`}
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// PerPrinterConfig
// ============================================================

function PerPrinterConfig({ printerId, config, onChange, materials }: {
  printerId: string;
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
  materials: string[];
}) {
  const printer = getPrinter(printerId);
  const presets = useMemo<ProcessPreset[]>(
    () => PROCESS_PRESETS.filter(p => p.printerId === printerId),
    [printerId],
  );
  const filaments = useMemo(
    () => filamentsForPrinter(printerId, materials),
    [printerId, materials],
  );

  const selectedPreset = presets.find(p => p.id === config.processId);
  const selectedFilOption = filaments.find(f => f.filament.id === config.filamentId);
  const selectedFilProfileIdx = config.profileIdx ?? 0;
  const selectedFilProfile = selectedFilOption?.profiles[selectedFilProfileIdx];

  if (!printer) return null;

  return (
    <div style={{
      padding: 14, background: 'var(--bg-1)',
      border: '1px solid var(--border-1)', borderRadius: 10,
    }}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className="elig on">{printer.badge}</span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{printer.nickname}</div>
          <div className="tiny muted">{printer.name}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="label">Process preset</label>
          <select
            className="select"
            value={config.processId ?? ''}
            onChange={e => onChange({ processId: e.target.value })}>
            {presets.length === 0 && <option value="">— no presets —</option>}
            {presets.map(p => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.layerHeight}mm
              </option>
            ))}
          </select>
          {selectedPreset && (
            <div className="tiny muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
              {selectedPreset.nozzle} · {selectedPreset.walls} walls · {selectedPreset.infill}% infill
            </div>
          )}
        </div>

        <div>
          <label className="label">Filament + profile</label>
          <select
            className="select"
            value={config.filamentId ? `${config.filamentId}::${selectedFilProfileIdx}` : ''}
            onChange={e => {
              const [filId, idxStr] = e.target.value.split('::');
              onChange({ filamentId: filId, profileIdx: Number(idxStr) || 0 });
            }}>
            {filaments.length === 0 && <option value="">— no compatible filaments —</option>}
            {filaments.flatMap(({ filament, profiles }) =>
              profiles.map((pf, i) => (
                <option key={`${filament.id}::${i}`} value={`${filament.id}::${i}`}>
                  {filament.name} · {pf.nozzle}
                </option>
              ))
            )}
          </select>
          {selectedFilProfile && (
            <div className="row gap-2" style={{ marginTop: 6, alignItems: 'center' }}>
              <FilamentDot color={selectedFilOption!.filament.color} />
              <span className="tiny muted">
                {selectedFilProfile.hotendTemp}° / {selectedFilProfile.bedTemp}° bed · {selectedFilProfile.layerHeight}mm
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// PlateConfigPanel
// ============================================================

function PlateConfigPanel({ plate, config, isMultiPlate, onSetField, onTogglePrinter, onSetPerPrinter, onSetOrders, onToggleQueued }: {
  plate: Plate;
  config: PlateConfig;
  isMultiPlate: boolean;
  onSetField: (patch: Partial<PlateConfig>) => void;
  onTogglePrinter: (id: string) => void;
  onSetPerPrinter: (printerId: string, patch: Partial<PerPrinterCfg>) => void;
  onSetOrders: (ids: string[]) => void;
  onToggleQueued: (v: boolean) => void;
}) {
  const eligibleIds = useMemo(() => eligibleForMaterial(plate.materials), [plate.materials]);
  const queued = !!config.selected;
  const totalParts = plate.parts.reduce((a, b) => a + b.qty, 0);

  return (
    <div className="col gap-4">
      {/* Plate banner with queue toggle */}
      <div className="row gap-3" style={{ alignItems: 'center' }}>
        <div style={{
          width: 56, height: 50, borderRadius: 8, flexShrink: 0,
          background: `linear-gradient(135deg, ${plate.thumbColor}, ${darken(plate.thumbColor)})`,
          border: '1px solid var(--border-2)',
          display: 'grid', placeItems: 'center',
          color: 'rgba(255,255,255,0.55)', fontSize: 10,
          fontFamily: 'var(--font-mono)',
          opacity: queued ? 1 : 0.55,
        }}>
          P{plate.index}
        </div>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: queued ? 'var(--text-1)' : 'var(--text-3)' }}>
            {plate.name}
          </div>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            {plate.materials.join(' · ')} · est. {fmtTime(plate.estTime)} · {totalParts} part{totalParts === 1 ? '' : 's'}
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
              eligibleIds={eligibleIds}
              selectedPrinters={config.selectedPrinters}
              onToggle={onTogglePrinter}
              materials={plate.materials}
            />
            {config.selectedPrinters.length > 0 && (
              <div className="col gap-3" style={{ marginTop: 14 }}>
                {config.selectedPrinters.map(pid => (
                  <PerPrinterConfig
                    key={pid}
                    printerId={pid}
                    config={config.perPrinter[pid] ?? { processId: null, filamentId: null, profileIdx: 0 }}
                    onChange={patch => onSetPerPrinter(pid, patch)}
                    materials={plate.materials}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Orders */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
              <StepNum n={3} done={config.orderIds.length > 0} />
              Fulfills orders
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              Link this plate to the customer or internal order{config.orderIds.length === 1 ? '' : 's'} its parts ship into. Optional, but helps track progress.
            </div>
            <OrdersPicker selectedOrderIds={config.orderIds} onChange={onSetOrders} />
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
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
              <div>
                <label className="label">Job name</label>
                <input
                  className="input"
                  value={config.jobName}
                  onChange={e => onSetField({ jobName: e.target.value })}
                  placeholder="e.g. PA-CF arm brackets"
                />
              </div>
              <div>
                <label className="label">Priority</label>
                <select
                  className="select"
                  value={config.priority}
                  onChange={e => onSetField({ priority: e.target.value })}>
                  <option value="rush">Rush — top of queue</option>
                  <option value="high">High</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low / fill</option>
                </select>
              </div>
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

  const allOrders = new Set<string>();
  selectedPlateIds.forEach(id => {
    (plateConfigs[id]?.orderIds ?? []).forEach(o => allOrders.add(o));
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
              <span
                key={id}
                className="mono tiny"
                style={{
                  padding: '2px 8px', background: 'var(--bg-1)',
                  border: '1px solid var(--border-1)',
                  borderRadius: 999, color: 'var(--text-2)',
                }}>
                {id}
              </span>
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

export function NewJobScreen() {
  const navigate = useNavigate();
  const [file, setFile] = useState<FileInfo | null>(null);
  const [plates, setPlates] = useState<Plate[]>([]);
  const [plateConfigs, setPlateConfigs] = useState<Record<string, PlateConfig>>({});
  const [activePlateId, setActivePlateId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
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
  function handleFile(rawFile: File | null | undefined) {
    if (!rawFile) return;
    const f: FileInfo = {
      name: rawFile.name,
      size: rawFile.size,
      type: rawFile.name.toLowerCase().endsWith('.stl') ? 'stl' : '3mf',
    };
    setFile(f);
    const detected = readPlatesFromFilename(f.name);
    setPlates(detected);
    const configs: Record<string, PlateConfig> = {};
    detected.forEach(p => { configs[p.id] = defaultConfigForPlate(p); });
    setPlateConfigs(configs);
    setActivePlateId(detected[0]?.id ?? null);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  function clearFile() {
    setFile(null); setPlates([]); setPlateConfigs({}); setActivePlateId(null);
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
      const plate = plates.find(p => p.id === plateId);
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
        const firstProcess = PROCESS_PRESETS.find(p => p.printerId === printerId);
        const firstFil = pickFilamentForPrinter(printerId, plate?.materials ?? []);
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: [...cfg.selectedPrinters, printerId],
            perPrinter: {
              ...cfg.perPrinter,
              [printerId]: {
                processId: firstProcess?.id ?? null,
                filamentId: firstFil?.id ?? null,
                profileIdx: firstFil?.profileIdx ?? 0,
              },
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

  function setOrdersForPlate(plateId: string, orderIds: string[]) {
    setPlateConfig(plateId, { orderIds });
  }

  // ---- validation ----
  const plateIsComplete = (plateId: string): boolean => {
    const cfg = plateConfigs[plateId];
    if (!cfg || !cfg.selected) return false;
    if (!cfg.jobName?.trim()) return false;
    if (cfg.selectedPrinters.length === 0) return false;
    return cfg.selectedPrinters.every(pid => {
      const pp = cfg.perPrinter[pid];
      return pp && pp.processId && pp.filamentId;
    });
  };

  const isComplete = selectedPlateIds.length > 0 && selectedPlateIds.every(plateIsComplete);

  function handleCreate() {
    const payload = {
      file,
      jobs: selectedPlateIds.map(id => ({
        plate: plates.find(p => p.id === id),
        config: plateConfigs[id],
      })),
    };
    console.log('New jobs:', payload);
    navigate('/queue');
  }

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate('/queue')}>{Icons.chevL} Queue</button>
        <span className="muted small">/</span>
        <span className="small">New job</span>
      </div>

      <div className="screen-grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 18 }}>
        <div className="col gap-4">

          {/* Step 1: source file */}
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader
              title={<span><StepNum n={1} done={!!file} /> Source file</span>}
              sub="Drop a .3mf with one or more plates, or a single .stl body."
            />
            {!file ? (
              <Dropzone
                dragOver={dragOver}
                onDragEnter={() => setDragOver(true)}
                onDragLeave={() => setDragOver(false)}
                onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              />
            ) : (
              <FileCard
                file={file}
                plateCount={plates.length}
                selectedCount={selectedPlateIds.length}
                onClear={clearFile}
              />
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
                  onSetField={patch => setPlateConfig(activePlateId, patch)}
                  onTogglePrinter={pid => togglePrinterForPlate(activePlateId, pid)}
                  onSetPerPrinter={(pid, patch) => setPerPrinter(activePlateId, pid, patch)}
                  onSetOrders={ids => setOrdersForPlate(activePlateId, ids)}
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
              disabled={!isComplete}
              onClick={handleCreate}>
              {Icons.check} Add {selectedPlateIds.length || ''} job{selectedPlateIds.length === 1 ? '' : 's'} to queue
            </button>
            <button
              className="btn ghost sm"
              style={{ width: '100%', marginTop: 8 }}
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
