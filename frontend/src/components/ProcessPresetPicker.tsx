import { useEffect, useState } from 'react';
import { getPrinterProfiles } from '../api/queue';

/** OrcaSlicer process preset picker for the project generate step. Offers only
 * presets compatible with every selected eligible printer, since generate_project
 * applies a single process_preset across all of them. */
export function ProcessPresetPicker({ printerIds, value, onChange }: {
  printerIds: number[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const [profiles, setProfiles] = useState<string[]>([]);
  const key = [...printerIds].sort((a, b) => a - b).join(',');

  useEffect(() => {
    if (printerIds.length === 0) { setProfiles([]); return; }
    let alive = true;
    Promise.all(
      printerIds.map(id => getPrinterProfiles(id).catch(() => ({ print_profiles: [], filament_profiles: [] }))),
    ).then(results => {
      if (!alive) return;
      const sets = results.map(r => new Set(r.print_profiles));
      const common = sets.length
        ? [...sets[0]].filter(name => sets.every(s => s.has(name))).sort()
        : [];
      setProfiles(common);
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Drop a selection that's no longer valid for the current printer set.
  useEffect(() => {
    if (value && !profiles.includes(value)) onChange(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles]);

  if (printerIds.length === 0) {
    return <div className="tiny muted">Select printers to choose a process preset.</div>;
  }

  return (
    <div>
      <label className="label">Process preset</label>
      <select
        data-testid="process-preset-select"
        className="select"
        value={value ?? ''}
        onChange={e => onChange(e.target.value || null)}
      >
        <option value="">— select process —</option>
        {profiles.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      {profiles.length === 0 && (
        <div className="tiny muted" style={{ marginTop: 4 }}>
          No process preset is compatible with every selected printer.
        </div>
      )}
    </div>
  );
}
