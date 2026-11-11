import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import {
  uploadFile,
  createJob,
  useQueueConfig,
  getFilePlates,
  getModelFilaments,
  getEmbeddedSettings,
  type EmbeddedSetting,
  type ModelFilament,
} from '../api/queue';
import { useFiles, getFiles, getSlicedVersions, type SlicedVersion } from '../api/files';
import { defaultPerPrinterCfg, type PerPrinterCfg } from '../components/PerPrinterConfig';
import { buildEligibility, modelKey } from '../components/ModelTargets';
import { fileKindOf, isPreslicedKind } from '../lib/fileKind';
import { staleLabels } from '../components/SliceCache';
import { FileInfo, Plate, PlateConfig, defaultConfigForPlate, platesToLocal, usePrinterList } from './newJob/model';
import { StepNum, TabDot } from './newJob/widgets';
import { Dropzone, FileCard } from './newJob/SourceFile';
import { PlateConfigPanel } from './newJob/PlateConfigPanel';
import { SummaryCard } from './newJob/SummaryCard';
import { CachedVersionPrompt } from './newJob/CachedVersionPrompt';
import { versionAsk } from './newJob/versions';

// ============================================================
// Types
// ============================================================

export function NewJobScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const printers = usePrinterList();
  // "Add job" from a project page arrives as /jobs/new?project=<id>: every plate starts linked to it.
  const initialProjectId = (() => {
    const raw = new URLSearchParams(location.search).get('project');
    const id = raw ? Number(raw) : NaN;
    return Number.isInteger(id) && id > 0 ? id : null;
  })();

  const [file, setFile] = useState<FileInfo | null>(null);
  const [uploadedFileId, setUploadedFileId] = useState<number | null>(null);
  const [plates, setPlates] = useState<Plate[]>([]);
  const [plateConfigs, setPlateConfigs] = useState<Record<string, PlateConfig>>({});
  const [modelFilaments, setModelFilaments] = useState<ModelFilament[]>([]);
  const [embeddedSettings, setEmbeddedSettings] = useState<EmbeddedSetting[]>([]);
  const [activePlateId, setActivePlateId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [eligibilityPrompt, setEligibilityPrompt] = useState<string | null>(null);   // unknown machine eligibility awaiting the user's say-so
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Source selection: upload a new file or pick one from the library.
  const [source, setSource] = useState<'upload' | 'library'>('upload');
  const { files: libraryFiles } = useFiles({});
  const [saveFolder, setSaveFolder] = useState('/Job Uploads');

  // Slicing cache (BIZ-194): keep the slice in the library, or print a cached version instead of slicing.
  const [saveSlice, setSaveSlice] = useState(false);
  const [saveSliceName, setSaveSliceName] = useState('');
  const [cachedFrom, setCachedFrom] = useState<{ modelId: number; model: FileInfo; version: SlicedVersion } | null>(null);
  const [versionPrompt, setVersionPrompt] = useState<{ fileId: number; plate: number; versions: SlicedVersion[] } | null>(null);
  const [dismissedPrompts, setDismissedPrompts] = useState<Set<string>>(new Set());
  const { config: queueConfig } = useQueueConfig();

  useEffect(() => {
    if (!successMsg) return;
    const t = setTimeout(() => setSuccessMsg(null), 4000);
    return () => clearTimeout(t);
  }, [successMsg]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectedPlateIds = useMemo(
    () => plates.filter(p => plateConfigs[p.id]?.selected).map(p => p.id),
    [plates, plateConfigs],
  );

  useEffect(() => {
    if (plates.length > 0 && !plates.find(p => p.id === activePlateId)) {
      setActivePlateId(plates[0].id);
    }
  }, [plates, activePlateId]);

  // Cached versions of this model for the plate being configured: offer them before the user sets up a reslice.
  const activePlateIndex = plates.find(p => p.id === activePlateId)?.index ?? null;
  useEffect(() => {
    if (uploadedFileId == null || activePlateIndex == null || !file || isPreslicedKind(file.type) || cachedFrom) {
      setVersionPrompt(null);
      return;
    }
    setVersionPrompt(null);   // never leave the previous file/plate's versions on screen while these load
    if (dismissedPrompts.has(`${uploadedFileId}:${activePlateIndex}`)) return;
    let alive = true;
    getSlicedVersions(uploadedFileId, activePlateIndex)
      .then(v => { if (alive) setVersionPrompt(v.length ? { fileId: uploadedFileId, plate: activePlateIndex, versions: v } : null); })
      .catch(() => { if (alive) setVersionPrompt(null); });
    return () => { alive = false; };
  }, [uploadedFileId, activePlateIndex, file, cachedFrom, dismissedPrompts]);

  function dismissPrompt() {
    if (versionPrompt) setDismissedPrompts(prev => new Set(prev).add(`${versionPrompt.fileId}:${versionPrompt.plate}`));
    setVersionPrompt(null);
  }

  async function useCachedVersion(version: SlicedVersion) {
    if (uploadedFileId == null || !file || versionPrompt?.fileId !== uploadedFileId) return;
    const model = { id: uploadedFileId, info: file };
    setUploading(true);
    setError(null);
    try {
      await loadFileIntoState(version.file_id, { name: version.name, size: 0, type: version.kind, folder: file.folder });
      setCachedFrom({ modelId: model.id, model: model.info, version });
      // Eligibility: any printer of the model(s) whose slicer profile it was sliced for (else the printers on that profile), asking
      // for the filament it was sliced with.
      const keys = version.model_uuids.length > 0
        ? version.model_uuids.map(modelKey)
        : printers.filter(p => p.machine_profile === version.machine_preset).map(p => String(p.id));
      setPlateConfigs(prev => {
        const next = { ...prev };
        for (const [id, cfg] of Object.entries(next)) {
          next[id] = {
            ...cfg, selectedPrinters: keys,
            perPrinter: Object.fromEntries(keys.map(k => [k, { ...defaultPerPrinterCfg(), ...versionAsk(version) }])),
          };
        }
        return next;
      });
    } catch (err) {
      setError(`Failed to load the cached version: ${err instanceof Error ? err.message : String(err)}`);
      setCachedFrom(null);
      await loadFileIntoState(model.id, model.info).catch(() => undefined);   // back to the model, not half-switched
    } finally {
      setUploading(false);
    }
  }

  async function resliceInstead() {
    const from = cachedFrom;
    if (!from) return;
    setDismissedPrompts(prev => new Set(prev).add(`${from.modelId}:${from.version.plate_number}`));
    setCachedFrom(null);
    await loadFileIntoState(from.modelId, from.model);
  }

  // ---- file actions ----

  // Shared path for both upload and library-pick: the /upload response no longer
  // carries a `plates` array, so plates are always loaded via getFilePlates(id).
  async function loadFileIntoState(fileId: number, fileInfo: FileInfo) {
    setUploadedFileId(fileId);
    setFile(fileInfo);
    const isGcode = isPreslicedKind(fileInfo.type);   // nothing to slice: no filament slots or embedded settings to read
    const [apiPlates, filaments, settings] = await Promise.all([
      getFilePlates(fileId),
      isGcode ? Promise.resolve([] as ModelFilament[]) : getModelFilaments(fileId).catch(() => [] as ModelFilament[]),
      isGcode ? Promise.resolve([] as EmbeddedSetting[]) : getEmbeddedSettings(fileId).catch(() => [] as EmbeddedSetting[]),
    ]);
    setModelFilaments(filaments);
    setEmbeddedSettings(settings);
    const detected = platesToLocal(apiPlates, fileId);
    setPlates(detected);
    const configs: Record<string, PlateConfig> = {};
    detected.forEach(p => { configs[p.id] = defaultConfigForPlate(p, initialProjectId); });
    setPlateConfigs(configs);
    setActivePlateId(detected[0]?.id ?? null);
  }

  async function handleFile(rawFile: File | null | undefined) {
    if (!rawFile) return;
    const nameLower = rawFile.name.toLowerCase();
    if (!nameLower.endsWith('.3mf') && !nameLower.endsWith('.stl') && !nameLower.endsWith('.gcode')) {
      setError('Only .3mf, .stl, .gcode and .gcode.3mf files are supported.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const uploaded = await uploadFile(rawFile, saveFolder || undefined);
      setCachedFrom(null);
      await loadFileIntoState(uploaded.id, {
        name: uploaded.original_filename,
        size: rawFile.size,
        type: fileKindOf(uploaded.original_filename),
        folder: uploaded.folder ?? (saveFolder || '/Job Uploads'),
      });
    } catch (err) {
      setError(`Upload failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  }

  async function selectLibraryFile(fileId: number) {
    setUploading(true);
    setError(null);
    try {
      // Prefer the already-loaded library list; fall back to a fetch if navigated
      // here directly (e.g. from FilesScreen) before the list resolves.
      let lib = libraryFiles.find(f => f.id === fileId);
      if (!lib) {
        const all = await getFiles({});
        lib = all.find(f => f.id === fileId);
      }
      if (!lib) {
        setError('That library file could not be found.');
        return;
      }
      setCachedFrom(null);
      await loadFileIntoState(lib.id, {
        name: lib.original_filename,
        size: lib.size_bytes,
        type: fileKindOf(lib.original_filename),
        folder: lib.folder,
      });
    } catch (err) {
      setError(`Failed to load file: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  }

  // Preselect a library file when navigated here from the Files screen
  // ("Use in new job" → state.libraryFileId).
  useEffect(() => {
    const id = (location.state as { libraryFileId?: number } | null)?.libraryFileId;
    if (id) { setSource('library'); selectLibraryFile(id); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  function clearFile() {
    setFile(null); setUploadedFileId(null); setPlates([]);
    setPlateConfigs({}); setActivePlateId(null); setError(null); setModelFilaments([]); setEmbeddedSettings([]);
    setCachedFrom(null); setVersionPrompt(null); setSaveSlice(false); setSaveSliceName('');
  }

  // ---- plate config mutators ----
  function setPlateConfig(plateId: string, patch: Partial<PlateConfig>) {
    setPlateConfigs(prev => ({ ...prev, [plateId]: { ...prev[plateId], ...patch } }));
  }

  function togglePlate(plateId: string, value: boolean) {
    setPlateConfig(plateId, { selected: value });
  }

  function togglePrinterForPlate(plateId: string, printerId: string) {
    setPlateConfigs(prev => {
      const cfg = prev[plateId];
      if (!cfg) return prev;
      const inSelection = cfg.selectedPrinters.includes(printerId);
      if (inSelection) {
        const nextPP = { ...cfg.perPrinter };
        delete nextPP[printerId];
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: cfg.selectedPrinters.filter(id => id !== printerId),
            perPrinter: nextPP,
          },
        };
      } else {
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: [...cfg.selectedPrinters, printerId],
            perPrinter: {
              ...cfg.perPrinter,
              [printerId]: defaultPerPrinterCfg(),
            },
          },
        };
      }
    });
  }

  function setPerPrinter(plateId: string, printerId: string, patch: Partial<PerPrinterCfg>) {
    setPlateConfigs(prev => ({
      ...prev,
      [plateId]: {
        ...prev[plateId],
        perPrinter: {
          ...prev[plateId].perPrinter,
          [printerId]: { ...prev[plateId].perPrinter[printerId], ...patch },
        },
      },
    }));
  }

  function setProjectForPlate(plateId: string, projectId: number | null) {
    setPlateConfig(plateId, { projectId });
  }

  // ---- validation ----
  const plateIsComplete = (plateId: string): boolean => {
    const cfg = plateConfigs[plateId];
    if (!cfg || !cfg.selected) return false;
    if (!cfg.jobName?.trim()) return false;
    if (cfg.selectedPrinters.length === 0) return false;
    return cfg.selectedPrinters.every(pid => {
      const pp = cfg.perPrinter[pid];
      // A pre-sliced gcode job has no print profile — naming the printer(s) or model(s) is enough.
      return !!pp && (isPreslicedKind(file?.type) || pp.printProfile != null);   // "" = Printer default
    });
  };

  const isComplete = uploadedFileId != null && selectedPlateIds.length > 0 && selectedPlateIds.every(plateIsComplete);

  async function doCreate(confirmUnknownEligibility = false) {
    if (!uploadedFileId) return;
    setSubmitting(true);
    setError(null);
    setEligibilityPrompt(null);
    const count = selectedPlateIds.length;
    const created: string[] = [];
    try {
      for (const id of selectedPlateIds) {
        const plate = plates.find(p => p.id === id)!;
        const cfg = plateConfigs[id];
        await createJob({
          uploaded_file_id: uploadedFileId,
          plate_number: plate.index,
          project_id: cfg.projectId,
          ...buildEligibility(cfg.selectedPrinters, cfg.perPrinter),
          ...(confirmUnknownEligibility ? { confirm_unknown_eligibility: true } : {}),
          overrides: Object.keys(cfg.confirmedOverrides).length > 0 ? cfg.confirmedOverrides : null,
          ...(saveSlice && !isPreslicedKind(file?.type)
            ? { save_slice: true, save_slice_name: saveSliceName.trim() || null } : {}),
        });
        created.push(id);
      }
      clearFile();
      setSuccessMsg(`${count} job${count === 1 ? '' : 's'} added to queue`);
    } catch (err) {
      // Plates that already went through must not be queued a second time when the operator retries:
      // take them out of the selection (they show as SKIP) and say so.
      if (created.length > 0) {
        setPlateConfigs(prev => {
          const next = { ...prev };
          for (const id of created) next[id] = { ...next[id], selected: false };
          return next;
        });
      }
      const already = created.length > 0 ? ` (${created.length} of ${count} already added; those plates are now skipped)` : '';
      const text = err instanceof Error ? err.message : String(err);
      if (created.length === 0 && text.startsWith('409') && /machine eligibility/.test(text)) {
        // A legacy pre-sliced file: nothing records which machines it is for. Ask, never guess (BIZ-263).
        let detail = text.replace(/^409\s*/, '');
        try { detail = JSON.parse(detail).detail ?? detail; } catch { /* plain text body */ }
        setEligibilityPrompt(String(detail));
        return;
      }
      setError(`Failed to create job: ${err instanceof Error ? err.message : String(err)}${already}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate('/queue')}>{Icons.chevL} Queue</button>
        <span className="muted small">/</span>
        <span className="small">New job</span>
      </div>

      {successMsg && (
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'color-mix(in oklch, var(--ok) 12%, transparent)', border: '1px solid color-mix(in oklch, var(--ok) 30%, transparent)',
          color: 'var(--ok)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          {Icons.check} {successMsg} — <button className="btn ghost sm" style={{ padding: '0 4px', color: 'inherit' }} onClick={() => navigate('/queue')}>view queue</button>
        </div>
      )}
      {eligibilityPrompt && (
        <div role="alert" data-testid="eligibility-prompt" style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'color-mix(in oklch, var(--warn) 12%, transparent)', border: '1px solid color-mix(in oklch, var(--warn) 30%, transparent)', fontSize: 13,
        }}>
          <div>{eligibilityPrompt}</div>
          <div className="row gap-2" style={{ marginTop: 8 }}>
            <button className="btn primary sm" disabled={submitting} onClick={() => doCreate(true)}>Send anyway — I confirm it fits</button>
            <button className="btn ghost sm" onClick={() => setEligibilityPrompt(null)}>Cancel</button>
          </div>
        </div>
      )}
      {error && (
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'color-mix(in oklch, var(--err) 12%, transparent)', border: '1px solid color-mix(in oklch, var(--err) 30%, transparent)',
          color: 'var(--err)', fontSize: 13,
        }}>
          {error}
        </div>
      )}

      <div className="layout-main-sidebar">
        <div className="col gap-4">

          {/* Step 1: source file */}
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader
              title={<span><StepNum n={1} done={!!file} /> Source file</span>}
              sub="Upload a new .3mf/.stl/.gcode/.gcode.3mf or pick one from your library."
            />

            {!file && !uploading && (
              <div className="row gap-2" style={{ marginBottom: 14 }}>
                <button
                  className={`btn sm ${source === 'upload' ? 'primary' : 'ghost'}`}
                  onClick={() => setSource('upload')}>
                  Upload
                </button>
                <button
                  className={`btn sm ${source === 'library' ? 'primary' : 'ghost'}`}
                  onClick={() => setSource('library')}>
                  Pick from library
                </button>
              </div>
            )}

            {uploading ? (
              <div className="tiny muted" style={{ padding: '24px 0', textAlign: 'center' }}>
                Loading and parsing plates…
              </div>
            ) : file ? (
              <FileCard
                file={file}
                plateCount={plates.length}
                selectedCount={selectedPlateIds.length}
                onClear={clearFile}
              />
            ) : source === 'library' ? (
              <div className="col gap-2">
                {libraryFiles.length === 0 ? (
                  <div className="tiny muted" style={{ padding: '12px 0' }}>
                    No files in the library yet. Upload one or add files in the Files screen.
                  </div>
                ) : (
                  libraryFiles.map(f => (
                    <button
                      key={f.id}
                      className="btn ghost"
                      style={{ justifyContent: 'space-between', width: '100%' }}
                      onClick={() => selectLibraryFile(f.id)}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.original_filename}
                      </span>
                      <span className="tiny muted" style={{ marginLeft: 12, flexShrink: 0 }}>{f.folder}</span>
                    </button>
                  ))
                )}
              </div>
            ) : (
              <div className="col gap-3">
                <Dropzone
                  dragOver={dragOver}
                  onDragEnter={() => setDragOver(true)}
                  onDragLeave={() => setDragOver(false)}
                  onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                />
                <div>
                  <label className="label">Save uploaded file to</label>
                  <input
                    className="input"
                    value={saveFolder}
                    onChange={e => setSaveFolder(e.target.value)}
                    placeholder="/Job Uploads"
                  />
                </div>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".3mf,.stl,.gcode"
              style={{ display: 'none' }}
              onChange={e => handleFile(e.target.files?.[0])}
            />
          </div>

          {cachedFrom && (
            <div className="card row between" data-testid="using-cached" style={{ padding: '10px 14px', alignItems: 'center', gap: 12 }}>
              <span className="small">
                Printing cached gcode <b>{cachedFrom.version.name}</b> (sliced from {cachedFrom.model.name}).
                {cachedFrom.version.stale && <> {staleLabels(cachedFrom.version.stale_reasons).join(', ')}.</>}
              </span>
              <button className="btn ghost sm" onClick={resliceInstead}>Reslice instead</button>
            </div>
          )}

          {versionPrompt && versionPrompt.fileId === uploadedFileId && versionPrompt.plate === activePlateIndex && (
            <CachedVersionPrompt
              multiPlate={plates.length > 1}
              plate={versionPrompt.plate}
              versions={versionPrompt.versions}
              useLatest={queueConfig?.slice_cache_use_latest_settings ?? true}
              onUse={useCachedVersion}
              onReslice={dismissPrompt}
            />
          )}

          {file && !isPreslicedKind(file.type) && (
            <div className="card col gap-2" style={{ padding: '12px 14px' }}>
              <label className="row gap-2 small" style={{ alignItems: 'center', cursor: 'pointer' }}>
                <input type="checkbox" checked={saveSlice} onChange={e => setSaveSlice(e.target.checked)} />
                Save sliced gcode to library
              </label>
              {saveSlice && (
                <>
                  <input className="input" aria-label="Saved gcode name" value={saveSliceName}
                         onChange={e => setSaveSliceName(e.target.value)}
                         placeholder={`${file.name.replace(/\.(3mf|stl)$/i, '')} - <filament> - <process> - <printer model>`} />
                  <span className="tiny muted">Saved next to {file.name}{file.folder ? ` in ${file.folder}` : ''} — reuse it next time instead of slicing again.</span>
                </>
              )}
            </div>
          )}

          {/* Step 2: per-plate config */}
          {plates.length > 0 && activePlateId && (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              {plates.length > 1 && (
                <div style={{
                  display: 'flex',
                  borderBottom: '1px solid var(--border-1)',
                  overflow: 'auto',
                }}>
                  {plates.map(plate => {
                    const cfg = plateConfigs[plate.id];
                    const isActive = activePlateId === plate.id;
                    const queued = !!cfg?.selected;
                    const complete = plateIsComplete(plate.id);
                    return (
                      <button
                        key={plate.id}
                        onClick={() => setActivePlateId(plate.id)}
                        style={{
                          padding: '12px 16px',
                          background: isActive ? 'var(--bg-1)' : 'transparent',
                          border: 'none',
                          borderBottom: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
                          color: isActive ? 'var(--text-1)' : queued ? 'var(--text-1)' : 'var(--text-4)',
                          fontFamily: 'inherit',
                          fontSize: 13,
                          fontWeight: isActive ? 500 : 400,
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          whiteSpace: 'nowrap',
                          flexShrink: 0,
                        }}>
                        <TabDot queued={queued} complete={complete} />
                        Plate {plate.index} · {cfg.jobName || plate.name}
                        {!queued && (
                          <span className="mono tiny" style={{
                            padding: '1px 6px', marginLeft: 4,
                            border: '1px solid var(--border-1)',
                            borderRadius: 4, color: 'var(--text-4)',
                            fontSize: 9.5, letterSpacing: '0.04em',
                          }}>
                            SKIP
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              <div style={{ padding: 20 }}>
                <PlateConfigPanel
                  plate={plates.find(p => p.id === activePlateId)!}
                  config={plateConfigs[activePlateId]}
                  isMultiPlate={plates.length > 1}
                  gcode={isPreslicedKind(file?.type)}
                  printers={printers}
                  modelFilaments={modelFilaments}
                  embeddedSettings={embeddedSettings}
                  onSetField={patch => setPlateConfig(activePlateId, patch)}
                  onTogglePrinter={pid => togglePrinterForPlate(activePlateId, pid)}
                  onSetPerPrinter={(pid, patch) => setPerPrinter(activePlateId, pid, patch)}
                  onSetProject={pid => setProjectForPlate(activePlateId, pid)}
                  onToggleQueued={v => togglePlate(activePlateId, v)}
                  onSetOverrides={v => setPlateConfig(activePlateId, { confirmedOverrides: v })}
                />
              </div>
            </div>
          )}
        </div>

        {/* Right rail: summary */}
        <div className="col gap-4">
          <SummaryCard
            file={file}
            plates={plates}
            plateConfigs={plateConfigs}
            selectedPlateIds={selectedPlateIds}
            activePlateId={activePlateId}
            plateIsComplete={plateIsComplete}
          />
          <div className="card" style={{ padding: 18 }}>
            <button
              className="btn primary"
              style={{ width: '100%' }}
              disabled={!isComplete || submitting}
              onClick={() => doCreate()}>
              {submitting
                ? 'Adding to queue…'
                : <>{Icons.check} Add {selectedPlateIds.length || ''} job{selectedPlateIds.length === 1 ? '' : 's'} to queue</>
              }
            </button>
            <button
              className="btn ghost sm"
              style={{ width: '100%', marginTop: 8 }}
              disabled={submitting}
              onClick={() => navigate('/queue')}>
              Cancel
            </button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: 'center', lineHeight: 1.5 }}>
              Each plate becomes its own job.<br />Slicing runs when a printer claims.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
