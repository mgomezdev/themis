import { useState, useMemo } from 'react';
import type { LoadedFilament } from '../api/printers';
import type { ApiSpool, ApiFilament } from '../api/spoolman';
import { parseOrcaProfiles, spoolDisplayName } from '../api/spoolman';

export interface SlotSpoolPickerProps {
  slot: LoadedFilament;
  printerPreset: string | null;
  spools: ApiSpool[];
  filaments: ApiFilament[];
  filamentProfiles: string[];
  onChange: (patch: Partial<LoadedFilament>) => void;
}

function spoolColor(spool: ApiSpool): string {
  return spool.filament.color_hex ? `#${spool.filament.color_hex}` : '#94a3b8';
}

function spoolRowLabel(spool: ApiSpool): string {
  const vendor = spool.filament.vendor?.name;
  return `#${spool.id} ${vendor ? `${vendor} ` : ''}${spool.filament.name} ${spool.filament.material}`;
}

export function SlotSpoolPicker({
  slot, printerPreset, spools, filaments, filamentProfiles, onChange,
}: SlotSpoolPickerProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const selectedSpool = useMemo(
    () => spools.find(s => String(s.id) === slot.spoolman_spool_id) ?? null,
    [spools, slot.spoolman_spool_id],
  );

  const isCustom = !slot.spoolman_spool_id;
  const isDegraded = !isCustom && !selectedSpool;
  const showCombobox = spools.length > 0;

  const resolvedProfiles = useMemo(() => {
    if (!selectedSpool || !printerPreset) return null;
    const full = filaments.find(f => f.id === selectedSpool.filament.id);
    if (!full) return null;
    const profiles = parseOrcaProfiles(full)[printerPreset];
    return profiles && profiles.length > 0 ? profiles : null;
  }, [selectedSpool, printerPreset, filaments]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return spools
      .filter(s => {
        if (!q) return true;
        const vendor = s.filament.vendor?.name ?? '';
        return (
          String(s.id).includes(q) ||
          s.filament.name.toLowerCase().includes(q) ||
          vendor.toLowerCase().includes(q) ||
          s.filament.material.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => {
        const aKey = `${a.filament.vendor?.name ?? ''} ${a.filament.name}`.toLowerCase();
        const bKey = `${b.filament.vendor?.name ?? ''} ${b.filament.name}`.toLowerCase();
        return aKey.localeCompare(bKey);
      });
  }, [spools, query]);

  function pickSpool(spool: ApiSpool) {
    const full = filaments.find(f => f.id === spool.filament.id);
    const profiles = full && printerPreset ? (parseOrcaProfiles(full)[printerPreset] ?? null) : null;
    onChange({
      spoolman_spool_id: String(spool.id),
      type: spool.filament.material,
      color: spool.filament.color_hex ? `#${spool.filament.color_hex}` : '',
      filament_profile: profiles?.length === 1 ? profiles[0] : null,
      name: spoolDisplayName(spool),
    });
    setQuery('');
    setOpen(false);
  }

  function clearSpool() {
    onChange({ spoolman_spool_id: null, filament_profile: null });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {isDegraded && (
        <div style={{
          fontSize: 12, color: 'var(--warn)', padding: '4px 8px',
          background: 'rgba(234,179,8,0.10)', border: '1px solid rgba(234,179,8,0.3)', borderRadius: 6,
        }}>
          Spool #{slot.spoolman_spool_id} not found in Spoolman
        </div>
      )}

      {showCombobox && (
        <div style={{ position: 'relative' }}>
          {selectedSpool ? (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '7px 10px', background: 'var(--bg-1)',
              border: '1px solid var(--border-1)', borderRadius: 8,
            }}>
              <span style={{ width: 12, height: 12, borderRadius: '50%', background: spoolColor(selectedSpool), flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: 13, color: 'var(--text-1)' }}>
                {spoolRowLabel(selectedSpool)} — {selectedSpool.remaining_weight}g remaining
              </span>
              <button
                onClick={clearSpool}
                aria-label="Clear spool selection"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', fontSize: 16, padding: 0, lineHeight: 1 }}
              >×</button>
            </div>
          ) : (
            <input
              className="input"
              placeholder="Search spools…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => setOpen(true)}
              onBlur={() => setTimeout(() => setOpen(false), 150)}
            />
          )}

          {open && !selectedSpool && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
              background: 'var(--bg-2)', border: '1px solid var(--border-2)',
              borderRadius: 8, marginTop: 4,
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              maxHeight: 240, overflowY: 'auto',
            }}>
              <div
                onMouseDown={clearSpool}
                style={{ padding: '9px 14px', cursor: 'pointer', fontSize: 13, color: 'var(--text-3)', borderBottom: '1px solid var(--border-1)' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
              >
                Custom
              </div>
              {filtered.length === 0 && (
                <div style={{ padding: '9px 14px', fontSize: 13, color: 'var(--text-3)' }}>No spools match</div>
              )}
              {filtered.map(spool => (
                <div
                  key={spool.id}
                  onMouseDown={() => pickSpool(spool)}
                  style={{ padding: '9px 14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}
                >
                  <span style={{ width: 12, height: 12, borderRadius: '50%', background: spoolColor(spool), flexShrink: 0 }} />
                  <span>{spoolRowLabel(spool)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {(isCustom || isDegraded) && (
        <>
          <input
            className="input"
            placeholder="Type (e.g. PLA)"
            value={slot.type}
            onChange={e => onChange({ type: e.target.value })}
          />
          <input
            className="input"
            placeholder="Color (#hex)"
            value={slot.color}
            onChange={e => onChange({ color: e.target.value })}
          />
          <select
            className="select"
            value={slot.filament_profile ?? ''}
            onChange={e => onChange({ filament_profile: e.target.value || null })}
          >
            <option value="">— no filament profile —</option>
            {filamentProfiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </>
      )}

      {!isCustom && !isDegraded && selectedSpool && (
        <>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{
              flex: 1, padding: '7px 10px', background: 'var(--bg-1)',
              border: '1px solid var(--border-1)', borderRadius: 8, fontSize: 13, color: 'var(--text-2)',
            }}>
              {selectedSpool.filament.material}
            </div>
            <div style={{
              width: 34, height: 34, borderRadius: 8, flexShrink: 0,
              background: spoolColor(selectedSpool), border: '1px solid var(--border-1)',
            }} />
          </div>
          <select
            className="select"
            value={slot.filament_profile ?? ''}
            onChange={e => onChange({ filament_profile: e.target.value || null })}
          >
            <option value="">— select filament profile —</option>
            {(resolvedProfiles ?? filamentProfiles).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {!resolvedProfiles && (
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>No mapped profiles — select manually</div>
          )}
        </>
      )}
    </div>
  );
}
