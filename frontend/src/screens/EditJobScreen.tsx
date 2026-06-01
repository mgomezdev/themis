import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import type { ApiPrinter } from '../api/printers';
import { getJobDetails, getPrinterProfiles, updateJobConfigs, type ApiJobDetails } from '../api/queue';
import { useSpoolmanConfig, useFilaments, filamentDisplayName } from '../api/spoolman';

// ---- types ----

interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;
  filamentId: number | null;
  filamentType: string | null;
  filamentColor: string | null;
}

// ---- hooks ----

function usePrinterList(): ApiPrinter[] {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  useEffect(() => {
    let alive = true;
    fetch('/api/v1/printers').then(r => r.json()).then(d => { if (alive) setPrinters(d); }).catch(console.error);
    return () => { alive = false; };
  }, []);
  return printers;
}

function usePrinterProfiles(printerId: number | null) {
  const [data, setData] = useState({ printProfiles: [] as string[], filamentProfiles: [] as string[] });
  useEffect(() => {
    if (printerId == null) return;
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => { if (alive) setData({ printProfiles: p.print_profiles, filamentProfiles: p.filament_profiles }); })
      .catch(console.error);
    return () => { alive = false; };
  }, [printerId]);
  return data;
}

// ---- Checkbox ----

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <div style={{
      width: 18, height: 18, flexShrink: 0, borderRadius: 5,
      border: `1.5px solid ${checked ? 'var(--accent)' : 'var(--border-2)'}`,
      background: checked ? 'var(--accent)' : 'transparent',
      display: 'grid', placeItems: 'center',
    }}>
      {checked && <span style={{ color: 'white', display: 'inline-flex', fontSize: 10 }}>✓</span>}
    </div>
  );
}

// ---- PrinterPicker ----

const BADGE: Record<string, string> = { elegoo_centauri: 'ECC', bambu: 'P1S' };

function PrinterPicker({ printers, selected, onToggle }: {
  printers: ApiPrinter[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  if (printers.length === 0) return <div className="tiny muted">No printers configured.</div>;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
      {printers.map(p => {
        const sid = String(p.id);
        const isSelected = selected.includes(sid);
        const badge = BADGE[p.printer_type] ?? p.printer_type.slice(0, 3).toUpperCase();
        return (
          <button key={p.id} onClick={() => onToggle(sid)} style={{
            padding: 12,
            background: isSelected ? 'var(--bg-3)' : 'var(--bg-1)',
            border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border-1)'}`,
            boxShadow: isSelected ? '0 0 0 1px var(--accent)' : 'none',
            borderRadius: 10, textAlign: 'left', cursor: 'pointer',
            color: 'var(--text-1)', fontFamily: 'inherit',
          }}>
            <div className="row gap-2" style={{ alignItems: 'center' }}>
              <span className={`elig ${isSelected ? 'on' : 'off'}`}
                    style={isSelected ? { background: 'rgba(59,130,246,0.20)' } : undefined}>
                {badge}
              </span>
              <div className="col" style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ fontWeight: 500 }}>{p.name}</div>
                <div className="tiny muted">{p.printer_type}</div>
              </div>
              <Checkbox checked={isSelected} />
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ---- PerPrinterConfigEditor ----

function PerPrinterConfigEditor({ printerId, printers, config, onChange }: {
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
  const [manualMode, setManualMode] = useState(
    () => !spoolmanActive || (config.filamentId === null && config.filamentColor !== null),
  );

  useEffect(() => {
    if ((!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!printer) return null;
  const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();

  const catalogValue = config.filamentId != null
    ? (filaments.find(f => f.id === config.filamentId) != null
        ? filamentDisplayName(filaments.find(f => f.id === config.filamentId)!)
        : '')
    : '';

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
        {/* Print profile */}
        <div>
          <label className="label">Print profile</label>
          <select className="select" value={config.printProfile ?? ''}
                  onChange={e => onChange({ printProfile: e.target.value || null })}>
            <option value="">— select profile —</option>
            {printProfiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {printProfiles.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>No profiles found for this printer</div>
          )}
        </div>

        {/* Filament */}
        <div>
          <label className="label">Filament</label>
          {spoolmanActive && !manualMode ? (
            <div className="col gap-1">
              <select className="select" value={catalogValue}
                      onChange={e => {
                        const v = e.target.value;
                        if (v === '__manual__') { setManualMode(true); onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null }); return; }
                        const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
                        onChange({ filamentProfile: v || null, filamentId: f?.id ?? null, filamentType: f?.material ?? null, filamentColor: f?.color_hex ? `#${f.color_hex}` : null });
                      }}>
                <option value="">— select filament —</option>
                {filaments.map(f => <option key={f.id} value={filamentDisplayName(f)}>{filamentDisplayName(f)} · {f.material}</option>)}
                <option value="__manual__">Enter manually…</option>
              </select>
              {filaments.length === 0 && <div className="tiny muted">No filaments in Spoolman</div>}
            </div>
          ) : (
            <div className="col gap-1">
              <div className="row gap-2">
                <input className="input" list="edit-filament-types" placeholder="Type (PLA, PETG…)"
                       value={config.filamentType ?? ''}
                       onChange={e => onChange({ filamentType: e.target.value || null, filamentProfile: e.target.value || null, filamentId: null })}
                       style={{ flex: 1 }} />
                {spoolmanActive && (
                  <button className="btn ghost sm" onClick={() => { setManualMode(false); onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null }); }}>
                    ↩ Catalog
                  </button>
                )}
              </div>
              <datalist id="edit-filament-types">
                {['PLA', 'PLA+', 'PETG', 'ABS', 'ASA', 'TPU', 'PA-CF', 'Nylon', 'PC'].map(t => <option key={t} value={t} />)}
              </datalist>
              <div className="row gap-2" style={{ alignItems: 'center' }}>
                <input type="color" value={config.filamentColor ?? '#888888'}
                       onChange={e => onChange({ filamentColor: e.target.value })}
                       style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }} />
                <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Main screen ----

export function EditJobScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const jobId = id ? Number(id) : null;

  const [job, setJob] = useState<ApiJobDetails | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const printers = usePrinterList();
  const [selectedPrinters, setSelectedPrinters] = useState<string[]>([]);
  const [perPrinter, setPerPrinter] = useState<Record<string, PerPrinterCfg>>({});

  useEffect(() => {
    if (jobId == null) return;
    let alive = true;
    getJobDetails(jobId).then(j => {
      if (!alive) return;
      setJob(j);
      const ids = j.printer_configs.map(c => String(c.printer_id));
      setSelectedPrinters(ids);
      const pp: Record<string, PerPrinterCfg> = {};
      for (const c of j.printer_configs) {
        pp[String(c.printer_id)] = {
          printProfile: c.print_profile,
          filamentProfile: c.filament_profile,
          filamentId: c.filament_id,
          filamentType: c.filament_type,
          filamentColor: c.filament_color,
        };
      }
      setPerPrinter(pp);
    }).catch(e => { if (alive) setLoadError(String(e)); });
    return () => { alive = false; };
  }, [jobId]);

  function togglePrinter(sid: string) {
    setSelectedPrinters(prev => {
      if (prev.includes(sid)) {
        const next = { ...perPrinter };
        delete next[sid];
        setPerPrinter(next);
        return prev.filter(id => id !== sid);
      }
      setPerPrinter(p => ({ ...p, [sid]: { printProfile: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null } }));
      return [...prev, sid];
    });
  }

  function patchPerPrinter(sid: string, patch: Partial<PerPrinterCfg>) {
    setPerPrinter(prev => ({ ...prev, [sid]: { ...prev[sid], ...patch } }));
  }

  const isComplete = selectedPrinters.length > 0 && selectedPrinters.every(sid => {
    const pp = perPrinter[sid];
    return !!(pp?.printProfile && pp?.filamentType && pp?.filamentColor);
  });

  async function handleSave() {
    if (!jobId || !isComplete) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateJobConfigs(jobId, selectedPrinters.map(sid => ({
        printer_id: Number(sid),
        print_profile: perPrinter[sid].printProfile!,
        filament_profile: perPrinter[sid].filamentProfile ?? null,
        filament_id: perPrinter[sid].filamentId ?? null,
        filament_type: perPrinter[sid].filamentType,
        filament_color: perPrinter[sid].filamentColor,
      })));
      navigate(`/jobs/${jobId}`);
    } catch (e) {
      setSaveError(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div className="col gap-3">
        <button className="btn ghost sm" style={{ alignSelf: 'flex-start' }} onClick={() => navigate(-1)}>{Icons.chevL} Back</button>
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>{loadError}</div>
      </div>
    );
  }

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate(`/jobs/${jobId}`)}>{Icons.chevL} Job #{jobId}</button>
        <span className="muted small">/</span>
        <span className="small">Edit settings</span>
      </div>

      {saveError && (
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>{saveError}</div>
      )}

      {job && (
        <div className="card" style={{ padding: '12px 16px', background: 'var(--bg-1)' }}>
          <div className="row gap-3" style={{ alignItems: 'center' }}>
            <span className="mono tiny muted">#{job.id}</span>
            <span className="small" style={{ fontWeight: 500 }}>
              Plate {job.plate_number}
              {job.file && <span className="muted" style={{ fontWeight: 400 }}> · {job.file.original_filename}</span>}
            </span>
            {job.status === 'blocked' && (
              <span className="tiny" style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(251,191,36,0.12)', color: 'var(--warn)', fontWeight: 500 }}>
                BLOCKED
              </span>
            )}
            {job.status === 'failed' && (
              <span className="tiny" style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(239,68,68,0.12)', color: 'var(--err)', fontWeight: 500 }}>
                FAILED
              </span>
            )}
          </div>
          {(job.block_reason ?? (job.printer_configs.find(c => c.slice_failed)?.slice_error)) && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {job.block_reason ?? job.printer_configs.find(c => c.slice_failed)?.slice_error}
            </div>
          )}
        </div>
      )}

      <div className="layout-main-sidebar" style={{ gridTemplateColumns: 'minmax(0,1fr) 280px' }}>
        <div className="col gap-4">
          {/* Printer picker */}
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader title="Eligible printers"
                           sub="Which printers may claim this job. Configure slicing settings for each." />
            <PrinterPicker printers={printers} selected={selectedPrinters} onToggle={togglePrinter} />
          </div>

          {/* Per-printer config */}
          {selectedPrinters.length > 0 && (
            <div className="card" style={{ padding: 20 }}>
              <SectionHeader title="Slicing settings"
                             sub="Print profile and filament for each eligible printer." />
              <div className="col gap-3">
                {selectedPrinters.map(sid => (
                  <PerPrinterConfigEditor
                    key={sid}
                    printerId={sid}
                    printers={printers}
                    config={perPrinter[sid] ?? { printProfile: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null }}
                    onChange={patch => patchPerPrinter(sid, patch)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="col gap-4">
          <div className="card" style={{ padding: 18 }}>
            <button className="btn primary" style={{ width: '100%' }}
                    disabled={!isComplete || saving || !job}
                    onClick={handleSave}>
              {Icons.check} {saving ? 'Saving…' : 'Save & re-queue'}
            </button>
            <button className="btn ghost sm" style={{ width: '100%', marginTop: 8 }}
                    disabled={saving}
                    onClick={() => navigate(`/jobs/${jobId}`)}>
              Cancel
            </button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: 'center', lineHeight: 1.5 }}>
              Saves new settings, clears any slice error, and re-queues at current position.
            </div>
          </div>

          {selectedPrinters.length > 0 && (
            <div className="card" style={{ padding: 18 }}>
              <div className="tag-key" style={{ marginBottom: 8 }}>Checklist</div>
              <div className="col gap-2">
                {selectedPrinters.map(sid => {
                  const pp = perPrinter[sid];
                  const printer = printers.find(p => String(p.id) === sid);
                  const done = !!(pp?.printProfile && pp?.filamentType && pp?.filamentColor);
                  return (
                    <div key={sid} className="row gap-2" style={{ alignItems: 'center' }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: done ? 'var(--ok)' : 'var(--warn)', boxShadow: done ? '0 0 4px var(--ok)' : 'none' }} />
                      <span className="small" style={{ flex: 1, minWidth: 0, color: done ? 'var(--text-1)' : 'var(--text-3)' }}>
                        {printer?.name ?? sid}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
