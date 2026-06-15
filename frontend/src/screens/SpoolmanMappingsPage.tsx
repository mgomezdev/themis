import { useEffect, useMemo, useState } from 'react';
import { fetchPrinters, type ApiPrinter } from '../api/printers';
import { getPrinterProfiles } from '../api/queue';
import { fetchFilaments, filamentDisplayName, parseOrcaProfiles, patchFilamentOrcaProfiles, type ApiFilament } from '../api/spoolman';
import { FilamentProfileMultiSelect } from '../components/FilamentProfileMultiSelect';

// Map from machine preset name → one printer ID that uses it (for profile lookup)
function buildPresetPrinterMap(printers: ApiPrinter[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const p of printers) {
    if (p.current_orca_printer_profile && !map.has(p.current_orca_printer_profile)) {
      map.set(p.current_orca_printer_profile, p.id);
    }
  }
  return map;
}

function FilamentCard({ filament, presetPrinterMap, presetProfiles, initiallyExpanded, onRemove }: {
  filament: ApiFilament;
  presetPrinterMap: Map<string, number>;
  presetProfiles: Map<string, string[]>;
  initiallyExpanded: boolean;
  onRemove: () => void;
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const [draft, setDraft] = useState<Record<string, string[]>>(() => parseOrcaProfiles(filament));
  const [saved, setSaved] = useState<Record<string, string[]>>(() => parseOrcaProfiles(filament));
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  function updatePreset(preset: string, profiles: string[]) {
    setDraft(d => {
      const next = { ...d };
      if (profiles.length === 0) delete next[preset];
      else next[preset] = profiles;
      return next;
    });
    setSaveMsg(null);
  }

  async function save() {
    setSaving(true);
    setSaveMsg(null);
    try {
      await patchFilamentOrcaProfiles(filament.id, draft);
      setSaved({ ...draft });
      setSaveMsg({ ok: true, text: 'Saved' });
      // If all presets cleared, trigger remove from page
      if (Object.keys(draft).length === 0) onRemove();
    } catch (e) {
      setSaveMsg({ ok: false, text: e instanceof Error ? e.message : 'Save failed' });
    } finally {
      setSaving(false);
    }
  }

  const color = filament.color_hex ? `#${filament.color_hex}` : '#888';
  const presets = Array.from(presetPrinterMap.keys());

  // Orphaned: presets in saved mapping not covered by any registered printer
  const orphanedPresets = Object.keys(saved).filter(p => !presetPrinterMap.has(p));

  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10, overflow: 'hidden' }}>
      <div
        className="row gap-3"
        style={{ padding: '12px 16px', cursor: 'pointer', alignItems: 'center' }}
        onClick={() => setExpanded(e => !e)}
      >
        <div style={{ width: 12, height: 12, borderRadius: 3, flexShrink: 0, background: color, border: '1px solid var(--border-2)' }} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{filamentDisplayName(filament)}</div>
          <div className="tiny muted">{filament.material}</div>
        </div>
        {Object.keys(saved).length > 0 && (
          <span className="tiny" style={{ color: 'var(--ok)' }}>
            {Object.keys(saved).length} preset{Object.keys(saved).length > 1 ? 's' : ''} mapped
          </span>
        )}
        <span className="tiny muted">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-1)', padding: '12px 16px' }}>
          <div className="col gap-3">
            {presets.map(preset => {
              const available = presetProfiles.get(preset) ?? [];
              return (
                <div key={preset} className="col gap-1">
                  <label className="tiny muted">{preset}</label>
                  <FilamentProfileMultiSelect
                    profiles={available}
                    selected={draft[preset] ?? []}
                    onChange={profiles => updatePreset(preset, profiles)}
                    emptyText="No compatible filament presets found for this printer"
                  />
                </div>
              );
            })}

            {orphanedPresets.map(preset => (
              <div key={preset} className="col gap-1">
                <label className="tiny" style={{ color: 'var(--warn)' }}>
                  {preset} <span className="muted">(no matching printer registered)</span>
                </label>
                <div className="tiny muted">
                  Saved: {(saved[preset] ?? []).join(', ') || '—'}
                </div>
              </div>
            ))}
          </div>

          <div className="row gap-2" style={{ marginTop: 14, alignItems: 'center' }}>
            <button
              className="btn primary sm"
              disabled={!dirty || saving}
              onClick={save}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {saveMsg && (
              <span className="tiny" style={{ color: saveMsg.ok ? 'var(--ok)' : 'var(--err)' }}>
                {saveMsg.text}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function SpoolmanMappingsPage() {
  const [filaments, setFilaments] = useState<ApiFilament[]>([]);
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  const [presetProfiles, setPresetProfiles] = useState<Map<string, string[]>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filaments actively being edited (added via search)
  const [activeIds, setActiveIds] = useState<Set<number>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);

  const presetPrinterMap = useMemo(() => buildPresetPrinterMap(printers), [printers]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([fetchFilaments(), fetchPrinters()])
      .then(async ([fils, prns]) => {
        if (!alive) return;
        setFilaments(fils);
        setPrinters(prns);

        // Seed activeIds from filaments that already have mappings
        const withMappings = new Set(
          fils.filter(f => Object.keys(parseOrcaProfiles(f)).length > 0).map(f => f.id)
        );
        setActiveIds(withMappings);

        // Fetch filament profiles per unique machine preset
        const presetMap = buildPresetPrinterMap(prns);
        const profileMap = new Map<string, string[]>();
        await Promise.all(
          Array.from(presetMap.entries()).map(async ([preset, printerId]) => {
            try {
              const p = await getPrinterProfiles(printerId);
              profileMap.set(preset, p.filament_profiles);
            } catch {
              profileMap.set(preset, []);
            }
          })
        );
        if (alive) setPresetProfiles(profileMap);
      })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Load failed'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const activeFilaments = useMemo(
    () => filaments.filter(f => activeIds.has(f.id)),
    [filaments, activeIds]
  );

  const searchResults = useMemo(() => {
    if (!searchQuery) return [];
    const q = searchQuery.toLowerCase();
    return filaments
      .filter(f => !activeIds.has(f.id))
      .filter(f =>
        filamentDisplayName(f).toLowerCase().includes(q) ||
        f.material.toLowerCase().includes(q) ||
        (f.vendor?.name ?? '').toLowerCase().includes(q)
      )
      .slice(0, 8);
  }, [filaments, activeIds, searchQuery]);

  function addFilament(f: ApiFilament) {
    setActiveIds(ids => new Set([...ids, f.id]));
    setSearchQuery('');
    setSearchOpen(false);
  }

  function removeFilament(id: number) {
    setActiveIds(ids => { const next = new Set(ids); next.delete(id); return next; });
  }

  if (loading) {
    return <div className="small muted" style={{ padding: 24 }}>Loading filaments…</div>;
  }
  if (error) {
    return (
      <div style={{ padding: '12px 16px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
        {error}
      </div>
    );
  }
  if (printers.filter(p => p.current_orca_printer_profile).length === 0) {
    return (
      <div className="card" style={{ padding: 28 }}>
        <div className="small muted">
          No printers with an OrcaSlicer machine preset configured. Set a machine preset in Fleet → Edit printer first.
        </div>
      </div>
    );
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <div style={{ marginBottom: 18 }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>Filament Profile Mappings</h2>
          <div className="muted small" style={{ marginTop: 4 }}>
            Map Spoolman filaments to OrcaSlicer filament presets per printer model. Saved to the filament's <code>orca_profiles</code> custom field in Spoolman.
          </div>
        </div>

        {/* Search / add filament */}
        <div style={{ position: 'relative', marginBottom: 16 }}>
          <input
            className="input"
            placeholder="Search filaments to configure…"
            value={searchQuery}
            onChange={e => { setSearchQuery(e.target.value); setSearchOpen(true); }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
          />
          {searchOpen && searchResults.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
              background: 'var(--bg-2)', border: '1px solid var(--border-2)',
              borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
              marginTop: 2,
            }}>
              {searchResults.map(f => (
                <div
                  key={f.id}
                  onMouseDown={() => addFilament(f)}
                  style={{ padding: '9px 14px', cursor: 'pointer', fontSize: 13, borderBottom: '1px solid var(--border-1)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <span style={{ fontWeight: 500 }}>{filamentDisplayName(f)}</span>
                  <span className="tiny muted" style={{ marginLeft: 8 }}>{f.material}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Filament cards */}
        {activeFilaments.length === 0 ? (
          <div className="tiny muted">
            No filaments configured yet. Search above to add one.
          </div>
        ) : (
          <div className="col gap-2">
            {activeFilaments.map(f => (
              <FilamentCard
                key={f.id}
                filament={f}
                presetPrinterMap={presetPrinterMap}
                presetProfiles={presetProfiles}
                initiallyExpanded={Object.keys(parseOrcaProfiles(f)).length === 0}
                onRemove={() => removeFilament(f.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
