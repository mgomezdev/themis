// Fleet: pick the spool / filament loaded in a printer's slots.
import { useState, useEffect } from 'react';
import { NEW_SLOT_COLOR } from '../../data/helpers';
import { Icons } from '../../components/icons';
import { fetchPrinter, updatePrinter, type LoadedFilament } from '../../api/printers';
import { useInventory, useSpools } from '../../api/inventory';
import { getPrinterProfiles } from '../../api/queue';
import { SlotSpoolPicker } from '../../components/SlotSpoolPicker';
import { useSlotConflicts } from '../../components/useSlotConflicts';

// Multi-slot filament editor. A printer can have N manually-defined slots
// (e.g. the Snapmaker U1 has 4 tools); each maps to a tool index Tn and carries
// its own type/color/name + OrcaSlicer filament profile + optional inventory spool.
export function FilamentPicker({ printerId, onClose, onSaved }: {
  printerId: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const inventory = useInventory();
  const spools = useSpools(!!inventory.plugin);
  const [machinePreset, setMachinePreset] = useState<string | null>(null);   // what the printer slices with (its model's profile)
  const [filamentProfiles, setFilamentProfiles] = useState<string[]>([]);
  const [slots, setSlots] = useState<LoadedFilament[]>([]);
  const [stored, setStored] = useState<LoadedFilament[]>([]);      // the slots as the server holds them
  const slotConflicts = useSlotConflicts(printerId, stored, slots);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => { if (alive) setFilamentProfiles(p.filament_profiles); })
      .catch(() => {});
    fetchPrinter(printerId)
      .then(p => {
        if (alive) {
          setSlots(p.loaded_filaments ?? []);
          setStored(p.loaded_filaments ?? []);
          setMachinePreset(p.machine_profile ?? null);
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [printerId]);

  const addSlot = () =>
    setSlots(s => [...s, { slot: s.length, filament_id: null, name: '', manufacturer: null, type: 'PLA', subtype: 'standard', color: NEW_SLOT_COLOR }]);
  const removeSlot = (i: number) =>
    setSlots(s => s.filter((_, idx) => idx !== i).map((x, idx) => ({ ...x, slot: idx })));
  const updateSlot = (i: number, patch: Partial<LoadedFilament>) =>
    setSlots(s => s.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printerId, {
        loaded_filaments: slots.map((s, idx) => ({ ...s, slot: idx })),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-1)' }}>
      <div className="col gap-3">
        {slots.map((s, i) => (
          <div key={i} className="card" style={{ padding: 10, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <span className="tiny muted mono" style={{ width: 28, flexShrink: 0 }}>T{i}</span>
              <input
                className="input"
                style={{ flex: '1 1 130px' }}
                placeholder="Filament name"
                value={s.name}
                onChange={e => updateSlot(i, { name: e.target.value })}
              />
              <button className="btn ghost icon sm" onClick={() => removeSlot(i)} title="Remove slot">
                {Icons.x}
              </button>
            </div>
            <SlotSpoolPicker
              slot={s}
              printerPreset={machinePreset}
              provider={inventory.id ?? ''}
              spools={spools}
              filamentProfiles={filamentProfiles}
              onChange={patch => updateSlot(i, patch)}
              conflict={slotConflicts.includes(i)}
            />
          </div>
        ))}
        <button className="btn ghost sm" onClick={addSlot} style={{ alignSelf: 'flex-start' }}>
          {Icons.plus} Add slot
        </button>
      </div>
      {error && <div className="tiny" style={{ color: 'var(--err)', marginTop: 8 }}>{error}</div>}
      <div className="row between" style={{ marginTop: 12, alignItems: 'center' }}>
        <span className="tiny muted">
          {slots.length === 0
            ? 'No filament — saving leaves this printer unloaded.'
            : `${slots.length} slot${slots.length > 1 ? 's' : ''}`}
        </span>
        <div className="row gap-2">
          <button className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : 'Save filaments'}
          </button>
        </div>
      </div>
    </div>
  );
}
