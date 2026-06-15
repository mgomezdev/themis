import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import type { ApiPrinter } from '../api/printers';
import { getJobDetails, updateJobConfigs, getModelFilaments, getEmbeddedSettings, type ApiJobDetails, type ModelFilament, type EmbeddedSetting } from '../api/queue';
import { PerPrinterConfig, defaultPerPrinterCfg, type PerPrinterCfg } from '../components/PerPrinterConfig';
import { OverridePanel } from '../components/OverridePanel';

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
  const [modelFilaments, setModelFilaments] = useState<ModelFilament[]>([]);
  const [embeddedSettings, setEmbeddedSettings] = useState<EmbeddedSetting[]>([]);
  const [confirmedOverrides, setConfirmedOverrides] = useState<Record<string, string>>({});

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
          toolIndex: c.tool_index ?? null,
          filamentMap: c.filament_map ?? null,
        };
      }
      setPerPrinter(pp);
      // Pre-populate overrides from saved job data
      setConfirmedOverrides(j.overrides ?? {});

      if (j.file?.id) {
        Promise.all([
          getModelFilaments(j.file.id).catch(() => [] as ModelFilament[]),
          getEmbeddedSettings(j.file.id).catch(() => [] as EmbeddedSetting[]),
        ]).then(([filaments, settings]) => {
          if (!alive) return;
          setModelFilaments(filaments);
          setEmbeddedSettings(settings);
        });
      }
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
      setPerPrinter(p => ({ ...p, [sid]: defaultPerPrinterCfg() }));
      return [...prev, sid];
    });
  }

  function patchPerPrinter(sid: string, patch: Partial<PerPrinterCfg>) {
    setPerPrinter(prev => ({ ...prev, [sid]: { ...prev[sid], ...patch } }));
  }

  const isComplete = selectedPrinters.length > 0 && selectedPrinters.every(sid => {
    const pp = perPrinter[sid];
    return !!(pp?.printProfile);
  });

  async function handleSave() {
    if (!jobId || !isComplete) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateJobConfigs(
        jobId,
        selectedPrinters.map(sid => ({
          printer_id: Number(sid),
          print_profile: perPrinter[sid].printProfile!,
          filament_profile: perPrinter[sid].filamentProfile ?? null,
          filament_id: perPrinter[sid].filamentId ?? null,
          filament_type: perPrinter[sid].filamentType,
          filament_color: perPrinter[sid].filamentColor,
          tool_index: perPrinter[sid].toolIndex ?? null,
          filament_map: perPrinter[sid].filamentMap ?? null,
        })),
        Object.keys(confirmedOverrides).length > 0 ? confirmedOverrides : null,
      );
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
                  <PerPrinterConfig
                    key={sid}
                    printerId={sid}
                    printers={printers}
                    config={perPrinter[sid] ?? defaultPerPrinterCfg()}
                    onChange={patch => patchPerPrinter(sid, patch)}
                    modelFilaments={modelFilaments}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Embedded settings overrides */}
          {embeddedSettings.length > 0 && (
            <div className="card" style={{ padding: 20 }}>
              <SectionHeader title="3MF Embedded Settings"
                             sub="Settings baked into the file. Check the ones you want to apply — unchecked ones use the profile default." />
              <OverridePanel
                settings={embeddedSettings}
                value={confirmedOverrides}
                onChange={setConfirmedOverrides}
              />
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
                  const done = !!(pp?.printProfile);
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
