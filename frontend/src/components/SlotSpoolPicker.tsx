import { useState, useMemo } from 'react';
import type { LoadedFilament } from '../api/printers';
import type { InvSpool } from '../api/inventory';
import { activeSlotRef, profileLinks, slotPatchForSpool, spoolColor, spoolDisplayName } from '../api/inventory';
import { slotBinding } from '../api/printers';

export interface SlotSpoolPickerProps {
  slot: LoadedFilament;
  printerPreset: string | null;
  /** The active inventory provider's id (a spool is bound as `{provider, spool_ref}`). */
  provider: string;
  spools: InvSpool[];
  filamentProfiles: string[];
  onChange: (patch: Partial<LoadedFilament>) => void;
}

function spoolRowLabel(spool: InvSpool): string {
  return `#${spool.ref} ${spoolDisplayName(spool)}${spool.material?.material ? ` ${spool.material.material}` : ''}`;
}

/** "412g left" (+ "not yet synced" while a queued deduction hasn't reached the provider). */
function remainingLabel(spool: InvSpool): string | null {
  if (spool.remaining_g == null) return null;
  return `${Math.round(spool.remaining_g)}g left${spool.unsynced ? ' · not yet synced' : ''}`;
}

/** "Shelf B · 412g" — where the spool lives and what's left, so the right one can be found and trusted. */
function spoolDetail(spool: InvSpool): string {
  return [spool.location?.trim() || null, remainingLabel(spool)].filter(Boolean).join(' · ');
}

export function SlotSpoolPicker({
  slot, printerPreset, provider, spools, filamentProfiles, onChange,
}: SlotSpoolPickerProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const boundRef = activeSlotRef(slot, provider);
  const binding = slotBinding(slot);
  const selectedSpool = useMemo(() => spools.find(s => s.ref === boundRef) ?? null, [spools, boundRef]);

  const isCustom = !binding;
  const isDegraded = !isCustom && !selectedSpool;
  const showCombobox = spools.length > 0;

  const resolvedProfiles = useMemo(() => {
    if (!selectedSpool || !printerPreset) return null;
    const profiles = profileLinks(selectedSpool.material)[printerPreset];
    return profiles && profiles.length > 0 ? profiles : null;
  }, [selectedSpool, printerPreset]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return spools
      .filter(s => {
        if (!q) return true;
        return (
          s.ref.toLowerCase().includes(q) ||
          spoolDisplayName(s).toLowerCase().includes(q) ||
          (s.material?.material ?? '').toLowerCase().includes(q) ||
          (s.location ?? '').toLowerCase().includes(q)
        );
      })
      .sort((a, b) => spoolDisplayName(a).toLowerCase().localeCompare(spoolDisplayName(b).toLowerCase()));
  }, [spools, query]);

  function pickSpool(spool: InvSpool) {
    onChange(slotPatchForSpool(spool, provider, printerPreset, slot));
    setQuery('');
    setOpen(false);
  }

  function clearSpool() {
    onChange({ inventory: null, filament_profile: null });
    setQuery('');
    setOpen(false);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {isDegraded && (
        <div style={{
          fontSize: 12, color: 'var(--warn)', padding: '4px 8px',
          background: 'rgba(234,179,8,0.10)', border: '1px solid rgba(234,179,8,0.3)', borderRadius: 6,
        }}>
          {binding && binding.provider !== provider
            ? `Spool #${binding.ref} belongs to another inventory (${binding.provider}), which isn't the active one`
            : `Spool #${binding?.ref} not found in the inventory`}
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
                {spoolRowLabel(selectedSpool)} — {remainingLabel(selectedSpool) ?? '— remaining'}
                {selectedSpool.location?.trim() && (
                  <span data-testid="spool-location" className="muted" style={{ display: 'block', fontSize: 12 }}>
                    Stored at {selectedSpool.location.trim()}
                  </span>
                )}
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
                  key={spool.ref}
                  onMouseDown={() => pickSpool(spool)}
                  style={{ padding: '9px 14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}
                >
                  <span style={{ width: 12, height: 12, borderRadius: '50%', background: spoolColor(spool), flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{spoolRowLabel(spool)}</span>
                  <span className="muted" style={{ fontSize: 12 }}>{spoolDetail(spool)}</span>
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
            aria-label={`Filament profile for slot ${slot.slot + 1}`}
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
              {selectedSpool.material?.material ?? '—'}
            </div>
            <div style={{
              width: 34, height: 34, borderRadius: 8, flexShrink: 0,
              background: spoolColor(selectedSpool), border: '1px solid var(--border-1)',
            }} />
          </div>
          <select
            className="select"
            aria-label={`Filament profile for slot ${slot.slot + 1}`}
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
