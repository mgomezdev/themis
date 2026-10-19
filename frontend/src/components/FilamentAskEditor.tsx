import { useState } from 'react';
import { materialDisplayName, type InvMaterial } from '../api/inventory';
import {
  ANY, FILAMENT_TYPES, SUBTYPES, askFromMaterial, normalizeAsk, normColor, type FilamentAsk,
} from '../lib/filamentAsk';
import { Icons } from './icons';

interface Props {
  value: FilamentAsk;
  onChange: (v: FilamentAsk) => void;
  /** The active inventory's materials (empty when there is none): picking one only FILLS the four parts. */
  materials?: InvMaterial[];
  catalogEnabled?: boolean;
  /** Prefix for the test ids / aria labels, so several editors on one screen stay distinguishable. */
  id?: string;
}

/**
 * Edits a filament ask: manufacturer, type, subtype, color. Every part can be left undefined ("matches anything"); a type always
 * carries a subtype (`any` = don't care, `standard` = plain filament). The tuple can be typed by hand — no inventory is needed — or
 * filled from an inventory material; the job keeps only the tuple, never a reference to the material.
 */
export function FilamentAskEditor({ value, onChange, materials = [], catalogEnabled = false, id = 'ask' }: Props) {
  const [picking, setPicking] = useState(false);
  const set = (patch: Partial<FilamentAsk>) => onChange(normalizeAsk({ ...value, ...patch }));
  const makers = [...new Set(materials.map(m => m.vendor).filter((v): v is string => !!v))].sort();
  const typeOptions = value.filament_type && !FILAMENT_TYPES.some(t => t.toLowerCase() === value.filament_type!.toLowerCase())
    ? [value.filament_type, ...FILAMENT_TYPES] : FILAMENT_TYPES;
  const subtypeOptions = value.filament_subtype && !SUBTYPES.includes(value.filament_subtype.toLowerCase())
    ? [...SUBTYPES, value.filament_subtype] : SUBTYPES;
  const hasType = !!value.filament_type;
  const color = value.filament_color;

  return (
    <div className="col gap-2">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        <input
          data-testid={`${id}-manufacturer`} aria-label="Manufacturer" className="input" list={`${id}-makers`}
          placeholder="Any maker" style={{ flex: '1 1 110px', minWidth: 0, fontSize: 12 }}
          value={value.filament_manufacturer ?? ''}
          onChange={e => onChange({ ...value, filament_manufacturer: e.target.value.trim() ? e.target.value : null })}   // keep inner spaces while typing ("Bambu Lab"); the server trims
        />
        <datalist id={`${id}-makers`}>{makers.map(m => <option key={m} value={m} />)}</datalist>

        <select
          data-testid={`${id}-type`} aria-label="Filament type" className="select"
          style={{ flex: '1 1 80px', minWidth: 0, fontSize: 12 }}
          value={value.filament_type ?? ''}
          onChange={e => set({ filament_type: e.target.value || null, ...(e.target.value ? {} : { filament_subtype: null }) })}
        >
          <option value="">Any type</option>
          {typeOptions.map(t => <option key={t} value={t}>{t}</option>)}
        </select>

        <select
          data-testid={`${id}-subtype`} aria-label="Filament subtype" className="select"
          style={{ flex: '1 1 80px', minWidth: 0, fontSize: 12 }}
          disabled={!hasType} title={hasType ? 'any = any variant, standard = plain filament' : 'Choose a type first'}
          value={hasType ? value.filament_subtype ?? ANY : ''}
          onChange={e => set({ filament_subtype: e.target.value })}
        >
          {!hasType && <option value="">—</option>}
          {subtypeOptions.map(s => <option key={s} value={s}>{s === ANY ? 'any subtype' : s}</option>)}
        </select>

        {color ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <input
              data-testid={`${id}-color`} aria-label="Filament color" type="color"
              value={`#${normColor(color)?.padEnd(6, '0').slice(0, 6) ?? '888888'}`}
              onChange={e => set({ filament_color: e.target.value })}
              style={{ width: 28, height: 28, padding: 2, borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer' }}
            />
            <button className="btn ghost icon sm" title="Any color" aria-label="Any color" onClick={() => set({ filament_color: null })}
                    style={{ padding: '0 4px' }}>{Icons.x}</button>
          </span>
        ) : (
          <button data-testid={`${id}-color`} className="btn ghost sm" style={{ fontSize: 11, whiteSpace: 'nowrap', padding: '0 8px' }}
                  title="Pick a specific color" onClick={() => set({ filament_color: '#1a73e8' })}>any color</button>
        )}

        {catalogEnabled && materials.length > 0 && (
          <button data-testid={`${id}-pick`} className="btn ghost sm" style={{ fontSize: 11, whiteSpace: 'nowrap', padding: '0 8px' }}
                  title="Fill the fields from an inventory material" onClick={() => setPicking(p => !p)}>
            {picking ? 'cancel' : 'pick…'}
          </button>
        )}
      </div>

      {picking && (
        <select
          data-testid={`${id}-material`} aria-label="Fill from material" className="select" style={{ fontSize: 12 }} defaultValue=""
          onChange={e => {
            const m = materials.find(x => x.ref === e.target.value);
            if (m) { onChange(normalizeAsk(askFromMaterial(m))); setPicking(false); }
          }}
        >
          <option value="">— fill from a material —</option>
          {materials.filter(m => !m.archived).map(m => (
            <option key={m.ref} value={m.ref}>
              {materialDisplayName(m)}{m.material ? ` (${m.material}${m.subtype && m.subtype !== 'standard' ? ` ${m.subtype}` : ''})` : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
