import { useEffect, useState } from 'react';
import type { ApiPrinter } from '../api/printers';
import { getPrinterProfiles, type FilamentMapEntry, type ModelFilament } from '../api/queue';
import { useInventory, useMaterials } from '../api/inventory';
import { useSlotConflicts } from './useSlotConflicts';
import { NO_ASK, askIsUndefined, describeAsk, firstMatchingSlot, type FilamentAsk } from '../lib/filamentAsk';
import { FilamentAskEditor } from './FilamentAskEditor';
import { FilamentProfileSelect } from './FilamentProfileSelect';

export interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;
  // The filament ask: null = undefined (matches anything). The job stores only this tuple, never a material reference.
  filamentManufacturer: string | null;
  filamentType: string | null;
  filamentSubtype: string | null;
  filamentColor: string | null;
  toolIndex: number | null;
  filamentMap: FilamentMapEntry[] | null;
}

export function defaultPerPrinterCfg(): PerPrinterCfg {
  return {
    printProfile: null, filamentProfile: null,
    filamentManufacturer: null, filamentType: null, filamentSubtype: null, filamentColor: null,
    toolIndex: null, filamentMap: null,
  };
}

/** The config's single-filament ask as a tuple (and back, for onChange). */
export const cfgAsk = (c: PerPrinterCfg): FilamentAsk => ({
  filament_manufacturer: c.filamentManufacturer, filament_type: c.filamentType,
  filament_subtype: c.filamentSubtype, filament_color: c.filamentColor,
});
export const askToCfg = (a: FilamentAsk): Pick<PerPrinterCfg, 'filamentManufacturer' | 'filamentType' | 'filamentSubtype' | 'filamentColor'> => ({
  filamentManufacturer: a.filament_manufacturer, filamentType: a.filament_type,
  filamentSubtype: a.filament_subtype, filamentColor: a.filament_color,
});

const BADGE: Record<string, string> = {
  bambu: 'P1S', elegoo_centauri: 'ECC', snapmaker_extended: 'U1',
};

/** A model colour pinned to tool `t` (the default: filament N on tool N-1, clamped to the slots there are). */
const pinned = (modelFilament: number, tool: number): FilamentMapEntry => ({ model_filament: modelFilament, tool_index: tool, ...NO_ASK });
const entryAsk = (e: FilamentMapEntry): FilamentAsk => ({
  filament_manufacturer: e.filament_manufacturer, filament_type: e.filament_type,
  filament_subtype: e.filament_subtype, filament_color: e.filament_color,
});

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

const CHIP = {
  ok: { background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.30)', color: 'var(--ok)' },
  warn: { background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.30)', color: 'var(--warn)' },
} as const;

/** Which slot would print this ask right now — a preview only; the queue decides (a job nothing matches simply waits, queued). */
function MatchChip({ ask, slots, conflicts, testId }: { ask: FilamentAsk; slots: ApiPrinter['loaded_filaments']; conflicts: number[]; testId?: string }) {
  const idx = firstMatchingSlot(ask, slots, conflicts);       // a slot in conflict with its bound spool is skipped, as the queue does
  const undefinedAsk = askIsUndefined(ask);
  const tone = idx !== null || undefinedAsk ? CHIP.ok : CHIP.warn;
  const text = idx !== null
    ? `✓ T${idx} · ${describeAsk({ ...NO_ASK, filament_type: slots[idx].type || null, filament_subtype: slots[idx].subtype ?? null, filament_color: slots[idx].color || null })}`
    : undefinedAsk ? '◉ Any loaded filament' : `⚠ nothing loaded matches ${describeAsk(ask)} — the job waits in the queue`;
  return (
    <span data-testid={testId} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, padding: '3px 8px', borderRadius: 4, ...tone }}>
      {text}
    </span>
  );
}

export function PerPrinterConfig({ printerId, printers, config, onChange, modelFilaments, gcode }: {
  printerId: string;
  printers: ApiPrinter[];
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
  modelFilaments?: ModelFilament[];
  /** Pre-sliced .gcode job: there is no slicing, so no print profile to choose. */
  gcode?: boolean;
}) {
  const pid = Number(printerId);
  const printer = printers.find(p => p.id === pid);
  const { printProfiles, filamentProfiles } = usePrinterProfiles(pid);
  const inventory = useInventory();
  const catalogActive = !!inventory.plugin;          // "a material catalog is available" (only to fill the tuple from)
  const materials = useMaterials(catalogActive);
  const conflicts = useSlotConflicts(pid, printer?.loaded_filaments);

  if (!printer) return null;
  const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();
  const slots = printer.loaded_filaments ?? [];
  const ask = cfgAsk(config);
  const multi = !!(modelFilaments && modelFilaments.length > 1 && slots.length >= 1);

  const profileSelect = !gcode && (
    <div style={{ marginTop: 12 }}>
      <label className="label">Filament profile (optional)</label>
      <FilamentProfileSelect
        profiles={filamentProfiles}
        value={config.filamentProfile ?? null}
        onChange={v => onChange({ filamentProfile: v })}
        placeholder="— use the loaded slot's profile —"
      />
    </div>
  );

  function mapEntries(): FilamentMapEntry[] {
    return config.filamentMap ?? modelFilaments!.map(mf => pinned(mf.index, Math.min(mf.index - 1, slots.length - 1)));
  }

  function setEntry(index: number, entry: FilamentMapEntry) {
    const next = mapEntries().filter(e => e.model_filament !== index);
    next.push(entry);
    next.sort((a, b) => a.model_filament - b.model_filament);
    onChange({ filamentMap: next });
  }

  return (
    <div style={{ padding: 14, background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10 }}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className="elig on">{badge}</span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{printer.name}</div>
          <div className="tiny muted">{printer.printer_type}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: gcode ? '1fr' : '1fr 1fr', gap: 12 }}>
        {!gcode && <div>
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
        </div>}

        {multi ? (
          <div>
            <label className="label">Filament mapping</label>
            <div className="col gap-2" style={{ marginTop: 4 }}>
              {modelFilaments!.map(f => {
                const entry = mapEntries().find(e => e.model_filament === f.index) ?? pinned(f.index, Math.min(f.index - 1, slots.length - 1));
                const byAsk = entry.tool_index === null;
                return (
                  <div key={f.index}>
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
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
                        style={{ flex: '0 0 auto', minWidth: 160 }}
                        value={byAsk ? 'ask' : `t:${entry.tool_index}`}
                        onChange={e => setEntry(f.index, e.target.value === 'ask'
                          ? { model_filament: f.index, tool_index: null, ...NO_ASK }
                          : pinned(f.index, Number(e.target.value.slice(2))))}
                      >
                        <option value="ask">Match by filament…</option>
                        <optgroup label="Pin to a tool">
                          {slots.map((s, i) => (
                            <option key={i} value={`t:${i}`}>T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}</option>
                          ))}
                        </optgroup>
                      </select>
                    </div>
                    {byAsk && (
                      <div className="col gap-2" style={{ marginLeft: 22, marginTop: 4 }}>
                        <FilamentAskEditor id={`map-ask-${f.index}`} value={entryAsk(entry)} materials={materials} catalogEnabled={catalogActive}
                                           onChange={a => setEntry(f.index, { model_filament: f.index, tool_index: null, ...a })} />
                        <div><MatchChip ask={entryAsk(entry)} slots={slots} conflicts={conflicts} testId={`map-match-${f.index}`} /></div>
                      </div>
                    )}
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
                      if (v === '') { onChange({ toolIndex: null }); return; }
                      const ti = Number(v);
                      onChange({ toolIndex: ti, filamentProfile: slots[ti]?.filament_profile ?? null, ...askToCfg(NO_ASK) });
                    }}>
              <option value="">Any tool (match by filament)</option>
              {slots.map((s, i) => (
                <option key={i} value={i}>T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}</option>
              ))}
            </select>
            {config.toolIndex == null ? (
              <div className="col gap-2" style={{ marginTop: 8 }}>
                <FilamentAskEditor id="filament-ask" value={ask} materials={materials} catalogEnabled={catalogActive}
                                   onChange={a => onChange(askToCfg(a))} />
                <div><MatchChip ask={ask} slots={slots} conflicts={conflicts} testId="filament-match" /></div>
              </div>
            ) : (
              <div className="tiny muted" style={{ marginTop: 4 }}>
                Prints on this physical tool whatever is loaded; its filament profile is used to slice.
              </div>
            )}
          </div>
        ) : (
          <div>
            <label className="label">Filament</label>
            <FilamentAskEditor id="filament-ask" value={ask} materials={materials} catalogEnabled={catalogActive}
                               onChange={a => onChange(askToCfg(a))} />
            <div style={{ marginTop: 8 }}><MatchChip ask={ask} slots={slots} conflicts={conflicts} testId="filament-match" /></div>
          </div>
        )}
      </div>

      {profileSelect}
    </div>
  );
}
