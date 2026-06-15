import { useEffect, useMemo, useState } from 'react';
import type { ApiPrinter } from '../api/printers';
import { getPrinterProfiles, type ModelFilament } from '../api/queue';
import { useSpoolmanConfig, useFilaments, filamentDisplayName, parseOrcaProfiles } from '../api/spoolman';
import { FilamentProfileSelect } from './FilamentProfileSelect';

export interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;
  filamentId: number | null;
  filamentType: string | null;
  filamentColor: string | null;
  toolIndex: number | null;
  filamentMap: { model_filament: number; tool_index: number }[] | null;
}

export function defaultPerPrinterCfg(): PerPrinterCfg {
  return {
    printProfile: null, filamentProfile: null, filamentId: null,
    filamentType: null, filamentColor: null, toolIndex: null,
    filamentMap: null,
  };
}

const BADGE: Record<string, string> = {
  bambu: 'P1S', elegoo_centauri: 'ECC', snapmaker_extended: 'U1',
};
const FILAMENT_TYPES = ['PLA', 'PLA+', 'PETG', 'ABS', 'ASA', 'TPU', 'Nylon', 'PC'];

function usePrinterProfiles(printerId: number | null): { printProfiles: string[]; filamentProfiles: string[] } {
  const [data, setData] = useState<{ printProfiles: string[]; filamentProfiles: string[] }>({
    printProfiles: [], filamentProfiles: [],
  });
  useEffect(() => {
    if (printerId == null) return;
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => { if (alive) setData({ printProfiles: p.print_profiles, filamentProfiles: p.filament_profiles }); })
      .catch(() => {});
    return () => { alive = false; };
  }, [printerId]);
  return data;
}

export function PerPrinterConfig({ printerId, printers, config, onChange, modelFilaments }: {
  printerId: string;
  printers: ApiPrinter[];
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
  modelFilaments?: ModelFilament[];
}) {
  const pid = Number(printerId);
  const printer = printers.find(p => p.id === pid);
  const { printProfiles, filamentProfiles } = usePrinterProfiles(pid);
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const filaments = useFilaments(spoolmanActive);

  const selectedFilament = config.filamentId != null
    ? filaments.find(f => f.id === config.filamentId) ?? null
    : null;

  const mappedProfiles: string[] | null = useMemo(() => {
    if (!selectedFilament || !printer?.current_orca_printer_profile) return null;
    const orcaProfiles = parseOrcaProfiles(selectedFilament);
    const list = orcaProfiles[printer.current_orca_printer_profile];
    return list && list.length > 0 ? list : null;
  }, [selectedFilament, printer]);

  useEffect(() => {
    if (mappedProfiles !== null && mappedProfiles.length === 1 && config.filamentProfile !== mappedProfiles[0]) {
      onChange({ filamentProfile: mappedProfiles[0] });
    }
    if (mappedProfiles === null && config.filamentId == null) {
      onChange({ filamentProfile: null });
    }
  }, [mappedProfiles, config.filamentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const [filamentConstraint, setFilamentConstraint] = useState<'defer' | 'type-only' | 'type-color'>(
    () => {
      if (!config.filamentType && !config.filamentId) return 'defer';
      if (config.filamentType && !config.filamentColor) return 'type-only';
      return 'type-color';
    },
  );
  const [manualMode, setManualMode] = useState(
    () => !spoolmanActive || (config.filamentId === null && !!config.filamentType),
  );

  useEffect(() => {
    if (filamentConstraint === 'type-color' && (!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode, filamentConstraint]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!printer) return null;
  const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();
  const slots = printer.loaded_filaments ?? [];
  const catalogValue = config.filamentId != null
    ? (filaments.find(f => f.id === config.filamentId) != null
        ? filamentDisplayName(filaments.find(f => f.id === config.filamentId)!) : '')
    : (config.filamentProfile ?? '');

  function clearAsk() {
    onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null });
  }

  function _normColor(c: string | null | undefined): string {
    return (c ?? '').replace('#', '').toLowerCase();
  }

  function computeSlotMatch(): { state: 'match' | 'no-match' | 'defer'; label: string; color: string | null } {
    if (filamentConstraint === 'defer') {
      return { state: 'defer', label: 'Any loaded filament', color: null };
    }
    const reqType = (config.filamentType ?? '').toLowerCase();
    const reqColor = _normColor(config.filamentColor);
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      const sType = (s.type ?? '').toLowerCase();
      if (!reqType) continue;
      if (sType === reqType) {
        if (filamentConstraint === 'type-only' || !reqColor || _normColor(s.color) === reqColor) {
          const label = `${s.type ?? '?'} · slot ${i}`;
          return { state: 'match', label, color: s.color ?? null };
        }
      }
    }
    const desc = filamentConstraint === 'type-only'
      ? `No ${config.filamentType ?? '?'} loaded`
      : `No ${config.filamentType ?? '?'} match`;
    return { state: 'no-match', label: desc, color: null };
  }

  const slotMatch = computeSlotMatch();

  return (
    <div style={{ padding: 14, background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10 }}>
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
          <select data-testid="print-profile-select" className="select"
                  value={config.printProfile ?? ''}
                  onChange={e => onChange({ printProfile: e.target.value || null })}>
            <option value="">— select profile —</option>
            {printProfiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {printProfiles.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>No profiles found for this printer</div>
          )}
        </div>

        {(modelFilaments && modelFilaments.length > 1 && slots.length >= 1) ? (
          <div>
            <label className="label">Filament mapping</label>
            <div className="col gap-2" style={{ marginTop: 4 }}>
              {modelFilaments.map(f => {
                // Build current map from config or use identity (f.index - 1, clamped)
                const currentMap: { model_filament: number; tool_index: number }[] =
                  config.filamentMap ??
                  modelFilaments.map(mf => ({
                    model_filament: mf.index,
                    tool_index: Math.min(mf.index - 1, slots.length - 1),
                  }));
                const entry = currentMap.find(e => e.model_filament === f.index);
                const currentToolIndex = entry != null ? entry.tool_index : Math.min(f.index - 1, slots.length - 1);

                function handleMapChange(chosenTool: number) {
                  // Start from current map (or identity) and replace/insert this filament's entry
                  const base: { model_filament: number; tool_index: number }[] =
                    config.filamentMap ??
                    modelFilaments!.map(mf => ({
                      model_filament: mf.index,
                      tool_index: Math.min(mf.index - 1, slots.length - 1),
                    }));
                  const newMap = base.filter(e => e.model_filament !== f.index);
                  newMap.push({ model_filament: f.index, tool_index: chosenTool });
                  // Sort by model_filament for stable ordering
                  newMap.sort((a, b) => a.model_filament - b.model_filament);
                  onChange({ filamentMap: newMap });
                }

                return (
                  <div key={f.index} className="row gap-2" style={{ alignItems: 'center' }}>
                    <div style={{
                      width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                      background: f.color || '#888', border: '1px solid var(--border-2)',
                    }} />
                    <span className="tiny" style={{ flex: 1, minWidth: 0, color: 'var(--text-2)' }}>
                      Filament {f.index}{f.type ? ` · ${f.type}` : ''}
                    </span>
                    <select
                      data-testid={`map-tool-${f.index}`}
                      className="select"
                      style={{ flex: '0 0 auto', minWidth: 110 }}
                      value={currentToolIndex}
                      onChange={e => handleMapChange(Number(e.target.value))}
                    >
                      {slots.map((s, i) => (
                        <option key={i} value={i}>T{i} · {s.type || '—'}</option>
                      ))}
                    </select>
                  </div>
                );
              })}
            </div>
          </div>
        ) : slots.length >= 2 ? (
          <div>
            <label className="label">Tool</label>
            <select data-testid="tool-select" className="select"
                    value={config.toolIndex ?? ''}
                    onChange={e => {
                      const v = e.target.value;
                      if (v === '') { onChange({ toolIndex: null }); clearAsk(); return; }
                      const ti = Number(v);
                      const s = slots[ti];
                      onChange({
                        toolIndex: ti,
                        filamentProfile: s?.filament_profile ?? null,
                        filamentId: null,
                        filamentType: s?.type ?? null,
                        filamentColor: s?.color ?? null,
                      });
                    }}>
              <option value="">Any / default tool</option>
              {slots.map((s, i) => (
                <option key={i} value={i}>T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}</option>
              ))}
            </select>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {config.toolIndex == null
                ? 'Prints on the default tool with whatever is loaded.'
                : 'Prints on this physical tool; its loaded filament profile is used to slice.'}
            </div>
          </div>
        ) : (
          <div>
            <label className="label">Filament</label>
            <select
              data-testid="filament-mode"
              className="select"
              value={filamentConstraint}
              onChange={e => {
                const mode = e.target.value as 'defer' | 'type-only' | 'type-color';
                setFilamentConstraint(mode);
                if (mode === 'defer') { clearAsk(); }
                else if (mode === 'type-only') { onChange({ filamentColor: null }); }
              }}
            >
              <option value="defer">Use loaded filament</option>
              <option value="type-only">Require by type</option>
              <option value="type-color">Require by type + color</option>
            </select>

            {filamentConstraint !== 'defer' && (
              <div style={{ marginTop: 8 }}>
                {spoolmanActive && !manualMode ? (
                  <select data-testid="filament-catalog-select" className="select" value={catalogValue}
                          onChange={e => {
                            const v = e.target.value;
                            if (v === '__manual__') { setManualMode(true); clearAsk(); return; }
                            const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
                            onChange({
                              filamentProfile: v || null, filamentId: f?.id ?? null,
                              filamentType: f?.material ?? null,
                              filamentColor: f?.color_hex ? `#${f.color_hex}` : null,
                            });
                            setFilamentConstraint('type-color');
                          }}>
                    <option value="">— select filament —</option>
                    {filaments.map(f => (
                      <option key={f.id} value={filamentDisplayName(f)}>{filamentDisplayName(f)} · {f.material}</option>
                    ))}
                    <option value="__manual__">Enter manually…</option>
                  </select>
                ) : (
                  <div className="col gap-2">
                    <div className="row gap-2">
                      <input data-testid="filament-type-input" className="input" list="filament-types"
                             placeholder="Type (PLA, PETG, ABS…)" value={config.filamentType ?? ''}
                             onChange={e => onChange({ filamentType: e.target.value || null, filamentProfile: e.target.value || null, filamentId: null })}
                             style={{ flex: 1 }} />
                      {spoolmanActive && (
                        <button className="btn ghost sm" onClick={() => { setManualMode(false); clearAsk(); }}>↩ Catalog</button>
                      )}
                    </div>
                    <datalist id="filament-types">
                      {FILAMENT_TYPES.map(t => <option key={t} value={t} />)}
                    </datalist>
                    {filamentConstraint === 'type-color' && (
                      <div className="row gap-2" style={{ alignItems: 'center' }}>
                        <input data-testid="filament-color-input" type="color" value={config.filamentColor ?? '#888888'}
                               onChange={e => onChange({ filamentColor: e.target.value })}
                               style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }} />
                        <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Chip badge — slot match status */}
            <div style={{ marginTop: 8 }}>
              {slotMatch.state === 'defer' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.30)',
                               color: 'var(--warn)' }}>
                  ◉ {slotMatch.label}
                </span>
              )}
              {slotMatch.state === 'match' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.30)',
                               color: 'var(--ok)' }}>
                  {slotMatch.color && (
                    <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0,
                                   background: slotMatch.color, display: 'inline-block' }} />
                  )}
                  ✓ {slotMatch.label}
                </span>
              )}
              {slotMatch.state === 'no-match' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.30)',
                               color: 'var(--err)' }}>
                  ✗ {slotMatch.label}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Filament profile section — appears when Spoolman filament is selected */}
      {spoolmanActive && config.filamentId != null && (
        <div style={{ marginTop: 12 }}>
          <label className="label">Filament profile</label>
          {mappedProfiles !== null && mappedProfiles.length === 1 ? (
            <div className="tiny" style={{ marginTop: 4, color: 'var(--text-2)' }}>
              {config.filamentProfile === mappedProfiles[0] ? (
                <>Auto-set: <span className="mono">{mappedProfiles[0]}</span> (from Spoolman mapping)</>
              ) : (
                <FilamentProfileSelect
                  profiles={mappedProfiles}
                  value={config.filamentProfile ?? null}
                  onChange={v => onChange({ filamentProfile: v })}
                  placeholder="— use printer default —"
                />
              )}
            </div>
          ) : (
            <FilamentProfileSelect
              profiles={mappedProfiles ?? filamentProfiles}
              value={config.filamentProfile ?? null}
              onChange={v => onChange({ filamentProfile: v })}
              placeholder="— use printer default —"
            />
          )}
          {mappedProfiles !== null && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {mappedProfiles.length === 1
                ? 'Profile set from Spoolman mapping. Select another to override.'
                : `Showing ${mappedProfiles.length} profiles from Spoolman mapping.`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
