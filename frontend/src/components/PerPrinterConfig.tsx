import { useEffect, useState } from 'react';
import type { ApiPrinter } from '../api/printers';
import { getPrinterProfiles } from '../api/queue';
import { useSpoolmanConfig, useFilaments, filamentDisplayName } from '../api/spoolman';

export interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;
  filamentId: number | null;
  filamentType: string | null;
  filamentColor: string | null;
  toolIndex: number | null;
}

export function defaultPerPrinterCfg(): PerPrinterCfg {
  return {
    printProfile: null, filamentProfile: null, filamentId: null,
    filamentType: null, filamentColor: null, toolIndex: null,
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

export function PerPrinterConfig({ printerId, printers, config, onChange }: {
  printerId: string;
  printers: ApiPrinter[];
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
}) {
  const pid = Number(printerId);
  const printer = printers.find(p => p.id === pid);
  const { printProfiles } = usePrinterProfiles(pid);
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const filaments = useFilaments(spoolmanActive);

  // Single-tool filament mode: 'defer' (use loaded) vs 'require'. Derived so Edit
  // Job restores it; default = defer.
  const [requireFilament, setRequireFilament] = useState(
    () => !!(config.filamentType || config.filamentProfile),
  );
  const [manualMode, setManualMode] = useState(
    () => !spoolmanActive || (config.filamentId === null && !!config.filamentType),
  );

  useEffect(() => {
    if (requireFilament && (!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode, requireFilament]); // eslint-disable-line react-hooks/exhaustive-deps

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

        {slots.length >= 2 ? (
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
            <select data-testid="filament-mode" className="select"
                    value={requireFilament ? 'require' : 'defer'}
                    onChange={e => {
                      const req = e.target.value === 'require';
                      setRequireFilament(req);
                      if (!req) clearAsk();
                    }}>
              <option value="defer">Use loaded filament</option>
              <option value="require">Require specific filament</option>
            </select>
            {requireFilament && (
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
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
                      <input data-testid="filament-color-input" type="color" value={config.filamentColor ?? '#888888'}
                             onChange={e => onChange({ filamentColor: e.target.value })}
                             style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }} />
                      <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
