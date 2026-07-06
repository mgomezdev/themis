import { useMemo } from 'react';
import type { OrcaFilament } from '../api/orca';

interface Props {
  filaments: OrcaFilament[];
  machineName: string;
  value: string;
  onChange: (uuid: string) => void;
  disabled?: boolean;
}

export function FilamentProfilePicker({ filaments, machineName, value, onChange, disabled }: Props) {
  const filtered = useMemo(() => {
    if (!machineName) return filaments;
    return filaments.filter(f =>
      !f.compatible_printers.length || f.compatible_printers.includes(machineName),
    );
  }, [filaments, machineName]);

  const byType = useMemo(() => {
    const groups: Record<string, OrcaFilament[]> = {};
    for (const f of filtered) {
      const t = f.filament_type || 'Other';
      (groups[t] ??= []).push(f);
    }
    return groups;
  }, [filtered]);

  return (
    <select
      className="select"
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={disabled || filtered.length === 0}
    >
      <option value="">— filament profile —</option>
      {Object.entries(byType)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([type, items]) => (
          <optgroup key={type} label={type}>
            {items.map(f => (
              <option key={f.uuid} value={f.uuid}>{f.display_name}</option>
            ))}
          </optgroup>
        ))}
    </select>
  );
}
