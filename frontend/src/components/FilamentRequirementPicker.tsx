import { useState } from 'react';
import type { ApiFilament } from '../api/spoolman';
import { filamentDisplayName } from '../api/spoolman';
import { Icons } from './icons';

const FILAMENT_TYPES = ['any', 'PLA', 'PLA+', 'PETG', 'ABS', 'ASA', 'TPU', 'Nylon', 'PC', 'HIPS', 'CF', 'GF'];

export interface FilamentRequirement {
  filament_type: string;    // "any" | "PLA" | ...
  filament_color: string;   // "any" | "#RRGGBB"
  filament_id: number | null;
}

interface Props {
  value: FilamentRequirement;
  onChange: (v: FilamentRequirement) => void;
  spoolmanFilaments: ApiFilament[];
  spoolmanEnabled: boolean;
}

export function FilamentRequirementPicker({ value, onChange, spoolmanFilaments, spoolmanEnabled }: Props) {
  const [showSpoolman, setShowSpoolman] = useState(false);

  const selectedFilament = value.filament_id != null
    ? spoolmanFilaments.find(f => f.id === value.filament_id)
    : null;

  // Spoolman specific filament is selected — show compact pill
  if (selectedFilament) {
    const swatch = selectedFilament.color_hex
      ? `#${selectedFilament.color_hex.replace(/^#/, '')}`
      : undefined;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {swatch && (
          <span style={{
            width: 14, height: 14, borderRadius: '50%',
            background: swatch, border: '1px solid var(--border)',
            flexShrink: 0,
          }} />
        )}
        <span style={{ fontSize: 12, color: 'var(--text-1)', flex: 1, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={filamentDisplayName(selectedFilament)}>
          {filamentDisplayName(selectedFilament)}
        </span>
        <button
          className="btn ghost icon sm"
          title="Clear specific filament"
          onClick={() => onChange({ filament_type: 'any', filament_color: 'any', filament_id: null })}
          style={{ padding: '0 4px', flexShrink: 0 }}
        >
          {Icons.x}
        </button>
      </div>
    );
  }

  // Type + color selectors (with optional Spoolman picker)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        {/* Type */}
        <select
          className="select"
          style={{ flex: 1, minWidth: 0, fontSize: 12 }}
          value={value.filament_type}
          onChange={e => onChange({ ...value, filament_type: e.target.value })}
        >
          {FILAMENT_TYPES.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        {/* Color — "any" checkbox + picker */}
        {value.filament_color === 'any' ? (
          <button
            className="btn ghost sm"
            style={{ fontSize: 11, whiteSpace: 'nowrap', padding: '0 8px' }}
            title="Pick a specific color"
            onClick={() => onChange({ ...value, filament_color: '#1a73e8' })}
          >
            any color
          </button>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <input
              type="color"
              value={value.filament_color}
              onChange={e => onChange({ ...value, filament_color: e.target.value })}
              style={{ width: 28, height: 28, padding: 2, borderRadius: 4,
                       border: '1px solid var(--border)', cursor: 'pointer' }}
              title="Filament color"
            />
            <button
              className="btn ghost icon sm"
              title="Any color"
              onClick={() => onChange({ ...value, filament_color: 'any' })}
              style={{ padding: '0 4px' }}
            >
              {Icons.x}
            </button>
          </div>
        )}

        {/* Spoolman picker toggle */}
        {spoolmanEnabled && spoolmanFilaments.length > 0 && (
          <button
            className="btn ghost sm"
            style={{ fontSize: 11, whiteSpace: 'nowrap', padding: '0 8px', flexShrink: 0 }}
            title="Pick a specific filament from Spoolman"
            onClick={() => setShowSpoolman(s => !s)}
          >
            {showSpoolman ? 'cancel' : 'pick…'}
          </button>
        )}
      </div>

      {/* Spoolman filament list */}
      {showSpoolman && (
        <select
          className="select"
          style={{ fontSize: 12 }}
          defaultValue=""
          onChange={e => {
            const id = parseInt(e.target.value);
            if (!isNaN(id)) {
              const f = spoolmanFilaments.find(x => x.id === id);
              onChange({
                filament_type: f?.material ?? 'any',
                filament_color: f?.color_hex ? `#${f.color_hex.replace(/^#/, '')}` : 'any',
                filament_id: id,
              });
              setShowSpoolman(false);
            }
          }}
        >
          <option value="">— select Spoolman filament —</option>
          {spoolmanFilaments.map(f => (
            <option key={f.id} value={f.id}>{filamentDisplayName(f)} ({f.material})</option>
          ))}
        </select>
      )}
    </div>
  );
}
