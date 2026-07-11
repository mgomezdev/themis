import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentProfilePicker } from '../components/FilamentProfilePicker';
import { useOrcaCatalog } from '../api/orca';
import type { OrcaMachine, OrcaProcess } from '../api/orca';
import { useFiles } from '../api/files';
import type { LibraryFile, FolderNode } from '../data/types';
import {
  getProject, createProject, patchProject,
  addProjectItem, updateProjectItem,
  generateProject,
  type ProjectItem,
} from '../api/projects';

// ---------------------------------------------------------------------------
// STL file tree helpers
// ---------------------------------------------------------------------------

function buildStlTree(files: LibraryFile[]): FolderNode {
  const root: FolderNode = { name: 'All files', path: '', count: 0, children: {} };
  for (const f of files) {
    const parts = f.folder.replace(/^\//, '').split('/').filter(Boolean);
    let node = root;
    for (const part of parts) {
      if (!node.children[part]) {
        node.children[part] = {
          name: part,
          path: parts.slice(0, parts.indexOf(part) + 1).join('/'),
          count: 0,
          children: {},
        };
      }
      node = node.children[part];
    }
  }
  return root;
}

function FolderRow({
  node, depth, selected, onSelect,
}: {
  node: FolderNode;
  depth: number;
  selected: string;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  const childKeys = Object.keys(node.children).sort();
  const isActive = selected === node.path;
  return (
    <div>
      <button
        onClick={() => { onSelect(node.path); if (childKeys.length) setOpen(o => !o); }}
        style={{
          width: '100%',
          padding: `5px 8px 5px ${8 + depth * 14}px`,
          background: isActive ? 'oklch(87% 0.185 95 / 0.10)' : 'transparent',
          border: 'none',
          color: isActive ? 'var(--accent)' : 'var(--text-2)',
          cursor: 'pointer',
          textAlign: 'left',
          fontSize: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          borderRadius: 4,
        }}
      >
        <span style={{ color: 'var(--text-4)', width: 12 }}>
          {childKeys.length ? (open ? Icons.chevD : Icons.chevR) : null}
        </span>
        <span style={{ color: isActive ? 'var(--accent-hi)' : 'var(--text-3)' }}>
          {depth === 0 ? Icons.files : Icons.files}
        </span>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </button>
      {open && childKeys.map(k => (
        <FolderRow key={k} node={node.children[k]} depth={depth + 1}
                   selected={selected} onSelect={onSelect} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local item row state (before saving)
// ---------------------------------------------------------------------------

interface LocalItem {
  localId: string;
  serverId?: number;
  file_id: number;
  file_name: string;
  quantity: number;
  filament_profile_uuid: string;
  color_hex: string;
  sort_order: number;
}

let _lid = 0;
const newLocalId = () => String(++_lid);

// ---------------------------------------------------------------------------
// Result panel
// ---------------------------------------------------------------------------

function ResultPanel({
  resultFileId,
  plateCount,
  onQueue,
  onViewFiles,
}: {
  resultFileId: number;
  plateCount: number;
  onQueue: () => void;
  onViewFiles: () => void;
}) {
  const [thumbnails, setThumbnails] = useState<{ plate_number: number; thumbnail_url: string }[]>([]);

  useEffect(() => {
    let retries = 0;
    let timer: ReturnType<typeof setTimeout>;

    function poll() {
      fetch(`/api/v1/files/${resultFileId}`)
        .then(r => r.json())
        .then(d => {
          const plates = (d.plate_thumbnails ?? []).filter((p: { thumbnail_url: string | null }) => p.thumbnail_url);
          if (plates.length > 0) {
            setThumbnails(plates);
          } else if (retries < 10) {
            retries++;
            timer = setTimeout(poll, 3000);
          }
        })
        .catch(() => {});
    }
    poll();
    return () => clearTimeout(timer);
  }, [resultFileId]);

  return (
    <div style={{
      border: '1px solid var(--ok)',
      borderRadius: 8,
      padding: 16,
      background: 'oklch(55% 0.15 142 / 0.06)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        {Icons.check}
        <strong style={{ fontSize: 14 }}>
          Arranged — {plateCount} plate{plateCount !== 1 ? 's' : ''}
        </strong>
      </div>
      {thumbnails.length > 0 && (
        <div style={{ display: 'flex', gap: 8, overflowX: 'auto', marginBottom: 12 }}>
          {thumbnails.map(p => (
            <div key={p.plate_number} style={{ textAlign: 'center', flexShrink: 0 }}>
              <img
                src={p.thumbnail_url}
                alt={`Plate ${p.plate_number}`}
                style={{ width: 120, height: 120, objectFit: 'contain', borderRadius: 6,
                         background: 'var(--bg-3)' }}
              />
              <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 4 }}>
                Plate {p.plate_number}
              </div>
            </div>
          ))}
        </div>
      )}
      {thumbnails.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 12 }}>
          Generating thumbnails…
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn primary sm" onClick={onQueue}>Add to Queue</button>
        <button className="btn sm" onClick={onViewFiles}>View in Files</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error banner
// ---------------------------------------------------------------------------

const GENERATE_ERRORS: Record<number, string> = {
  422: 'Add at least one part before generating.',
  502: 'Orca sidecar is offline. Check the container.',
  504: 'Generation timed out. Try fewer parts or reduce quantities.',
};

function parseGenerateError(msg: string): string {
  const code = parseInt(msg);
  if (!isNaN(code) && GENERATE_ERRORS[code]) return GENERATE_ERRORS[code];
  if (msg.includes('filament')) return 'Every part needs a filament profile before generating.';
  if (msg.includes('STL') || msg.includes('stl')) return 'One or more STL files are missing. Remove and re-add them.';
  return `Generation failed: ${msg}`;
}

// ---------------------------------------------------------------------------
// ProjectBuilderScreen
// ---------------------------------------------------------------------------

export function ProjectBuilderScreen() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? parseInt(id) : null;
  const navigate = useNavigate();

  const { catalog, loading: catalogLoading } = useOrcaCatalog();

  // Form state
  const [name, setName] = useState('');
  const [machineUuid, setMachineUuid] = useState('');
  const [processUuid, setProcessUuid] = useState('');
  const [items, setItems] = useState<LocalItem[]>([]);
  const [serverItems, setServerItems] = useState<Map<number, ProjectItem>>(new Map());
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [legacyResult, setLegacyResult] = useState<{
    resultFileId: number; plateCount: number;
  } | null>(null);
  const [generateResult, setGenerateResult] = useState<{ orderId: number | null } | null>(null);

  // Selected machine name (for filament filtering)
  const machineName = catalog?.machine.find(m => m.uuid === machineUuid)?.name ?? '';

  // Process options filtered by machine
  const processOptions: OrcaProcess[] = (catalog?.process ?? []).filter(p =>
    !p.compatible_printers.length || p.compatible_printers.includes(machineName),
  );

  // File tree state
  const { files: allFiles } = useFiles({});
  const stlFiles = allFiles.filter(f =>
    f.original_filename.toLowerCase().endsWith('.stl') && !f.missing,
  );
  const [selectedFolder, setSelectedFolder] = useState('');
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = stlFiles.filter(f => {
    const norm = f.folder.replace(/^\//, '');
    return selectedFolder === '' ? true : norm === selectedFolder;
  });

  // Load existing project
  useEffect(() => {
    if (!projectId) return;
    getProject(projectId).then(p => {
      setName(p.name);
      setMachineUuid(p.machine_uuid);
      setProcessUuid(p.process_uuid);
      setItems(p.items.map(it => ({
        localId: newLocalId(),
        serverId: it.id,
        file_id: it.file_id,
        file_name: it.file_name,
        quantity: it.quantity,
        filament_profile_uuid: it.filament_profile_uuid,
        color_hex: it.color_hex,
        sort_order: it.sort_order,
      })));
      setServerItems(new Map(p.items.map(it => [it.id, it])));
      if (p.result_file_id) {
        // Legacy single-result display (projects arranged before the generate flow)
        setLegacyResult({ resultFileId: p.result_file_id, plateCount: 0 });
      }
    }).catch(console.error);
  }, [projectId]);

  // Add file to items
  function addFile(f: LibraryFile) {
    setItems(prev => [
      ...prev,
      {
        localId: newLocalId(),
        file_id: f.id,
        file_name: f.original_filename,
        quantity: 1,
        filament_profile_uuid: '',
        color_hex: '#ffffff',
        sort_order: prev.length,
      },
    ]);
  }

  // Add variation (duplicate row)
  function addVariation(idx: number) {
    const src = items[idx];
    const variation: LocalItem = {
      localId: newLocalId(),
      file_id: src.file_id,
      file_name: src.file_name,
      quantity: 1,
      filament_profile_uuid: '',
      color_hex: '#ffffff',
      sort_order: items.length,
    };
    setItems(prev => [...prev.slice(0, idx + 1), variation, ...prev.slice(idx + 1)]);
  }

  function updateItem(localId: string, patch: Partial<LocalItem>) {
    setItems(prev => prev.map(it => it.localId === localId ? { ...it, ...patch } : it));
  }

  function removeItem(localId: string) {
    setItems(prev => prev.filter(it => it.localId !== localId));
  }

  // Save project (create or update)
  async function saveProject(): Promise<number> {
    if (projectId) {
      await patchProject(projectId, { name, machine_uuid: machineUuid, process_uuid: processUuid });
      // Sync items: delete removed server items, add new ones, update changed ones
      const existing = items.filter(i => i.serverId);
      const newOnes = items.filter(i => !i.serverId);
      for (const it of existing) {
        await updateProjectItem(projectId, it.serverId!, {
          quantity: it.quantity,
          filament_profile_uuid: it.filament_profile_uuid,
          color_hex: it.color_hex,
          sort_order: it.sort_order,
        });
      }
      for (let i = 0; i < newOnes.length; i++) {
        const it = newOnes[i];
        await addProjectItem(projectId, {
          file_id: it.file_id,
          quantity: it.quantity,
          filament_profile_uuid: it.filament_profile_uuid,
          color_hex: it.color_hex,
          sort_order: it.sort_order,
        });
      }
      return projectId;
    } else {
      const proj = await createProject({ name, machine_uuid: machineUuid, process_uuid: processUuid });
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        await addProjectItem(proj.id, {
          file_id: it.file_id,
          quantity: it.quantity,
          filament_profile_uuid: it.filament_profile_uuid,
          color_hex: it.color_hex,
          sort_order: i,
        });
      }
      return proj.id;
    }
  }

  async function handleSave() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const pid = await saveProject();
      if (!projectId) navigate(`/projects/${pid}`, { replace: true });
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  async function handleGenerate() {
    if (!name.trim() || items.length === 0) return;
    setSaving(true);
    setGenerateError('');
    setGenerateResult(null);
    try {
      const pid = await saveProject();
      if (!projectId) navigate(`/projects/${pid}`, { replace: true });
      setGenerating(true);
      const result = await generateProject(pid);
      setGenerateResult({ orderId: result.order_id });
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

  const canSave = name.trim().length > 0 && !saving && !generating;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, height: '100%' }}>
      {/* Left: STL file tree */}
      <div className="card" style={{ padding: 10, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 0 }}>
        <div style={{ padding: '4px 6px 8px', fontSize: 11, fontWeight: 600,
                      color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          STL Files
        </div>
        <FolderRow node={folderTree} depth={0} selected={selectedFolder} onSelect={setSelectedFolder} />
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }}>
          {folderFiles.length === 0 && (
            <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text-4)' }}>
              No STL files here
            </div>
          )}
          {folderFiles.map(f => (
            <button
              key={f.id}
              onClick={() => addFile(f)}
              title={`Add ${f.original_filename}`}
              style={{
                width: '100%',
                padding: '5px 10px',
                background: 'transparent',
                border: 'none',
                color: 'var(--text-2)',
                cursor: 'pointer',
                textAlign: 'left',
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                borderRadius: 4,
              }}
            >
              <span style={{ color: 'var(--text-4)', flexShrink: 0 }}>{Icons.files}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {f.original_filename}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Right: project form + items */}
      <div className="card" style={{ padding: 20, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
        {/* Header fields */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label" htmlFor="proj-name">Project name *</label>
            <input
              id="proj-name"
              className="input"
              placeholder="e.g. Gridfinity Tray Set"
              value={name}
              onChange={e => setName(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Machine profile</label>
            <select
              className="select"
              value={machineUuid}
              onChange={e => { setMachineUuid(e.target.value); setProcessUuid(''); }}
            >
              <option value="">— select machine —</option>
              {(catalog?.machine ?? []).map((m: OrcaMachine) => (
                <option key={m.uuid} value={m.uuid}>{m.name}</option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Process profile</label>
            <select
              className="select"
              value={processUuid}
              onChange={e => setProcessUuid(e.target.value)}
              disabled={!machineUuid || processOptions.length === 0}
            >
              <option value="">— select process —</option>
              {processOptions.map(p => (
                <option key={p.uuid} value={p.uuid}>{p.name}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Item table */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Parts ({items.length})
            </span>
            {catalogLoading && (
              <span style={{ fontSize: 11, color: 'var(--text-4)' }}>Loading catalog…</span>
            )}
          </div>
          {items.length === 0 ? (
            <div style={{
              border: '1px dashed var(--border)', borderRadius: 6,
              padding: '24px 16px', textAlign: 'center',
              color: 'var(--text-4)', fontSize: 13,
            }}>
              Click an STL file on the left to add parts
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {/* Column headers */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: '1fr 80px 1fr 80px auto auto',
                gap: 8,
                padding: '0 4px',
                fontSize: 11,
                fontWeight: 500,
                color: 'var(--text-4)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}>
                <span>File</span>
                <span>Qty</span>
                <span>Filament</span>
                <span>Color</span>
                <span />
                <span />
              </div>
              {items.map((it, idx) => (
                <div key={it.localId} style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 80px 1fr 80px auto auto',
                  gap: 8,
                  alignItems: 'center',
                }}>
                  <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
                    <span style={{
                      fontSize: 13, color: 'var(--text-1)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }} title={it.file_name}>
                      {it.file_name}
                    </span>
                    {it.serverId && (() => {
                      const si = serverItems.get(it.serverId!);
                      if (!si || (si.quantity_completed === 0 && si.quantity_failed === 0)) return null;
                      return (
                        <span style={{ fontSize: 11, color: si.quantity_failed > 0 ? 'var(--err)' : 'var(--text-3)', whiteSpace: 'nowrap' }}>
                          {si.quantity_completed}/{si.quantity_completed + si.quantity_failed} ok
                          {si.quantity_failed > 0 && ` · ${si.quantity_failed} failed`}
                        </span>
                      );
                    })()}
                  </span>
                  <input
                    type="number"
                    className="input"
                    min={1}
                    max={99}
                    value={it.quantity}
                    onChange={e => updateItem(it.localId, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                    style={{ textAlign: 'center' }}
                  />
                  <FilamentProfilePicker
                    filaments={catalog?.filament ?? []}
                    machineName={machineName}
                    value={it.filament_profile_uuid}
                    onChange={v => updateItem(it.localId, { filament_profile_uuid: v })}
                  />
                  <input
                    type="color"
                    value={it.color_hex}
                    onChange={e => updateItem(it.localId, { color_hex: e.target.value })}
                    style={{ width: '100%', height: 32, padding: 2, borderRadius: 4,
                             border: '1px solid var(--border)', cursor: 'pointer' }}
                    title="Filament color"
                  />
                  <button
                    className="btn ghost sm"
                    onClick={() => addVariation(idx)}
                    title="Add variation (same file, different material)"
                    style={{ fontSize: 11, whiteSpace: 'nowrap' }}
                  >
                    + Variation
                  </button>
                  <button
                    className="btn ghost icon sm"
                    onClick={() => removeItem(it.localId)}
                    title="Remove"
                  >
                    {Icons.x}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Legacy arranged result (projects generated before the generate flow) */}
        {legacyResult && (
          <ResultPanel
            resultFileId={legacyResult.resultFileId}
            plateCount={legacyResult.plateCount}
            onQueue={() => navigate('/queue/new', { state: { libraryFileId: legacyResult!.resultFileId } })}
            onViewFiles={() => navigate('/files')}
          />
        )}

        {/* Generation success */}
        {generateResult && (
          <div style={{
            border: '1px solid var(--ok)',
            borderRadius: 8,
            padding: '12px 16px',
            background: 'oklch(55% 0.15 142 / 0.06)',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}>
            <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
            <span style={{ fontSize: 14, flex: 1 }}>Jobs added to queue</span>
            {generateResult.orderId && (
              <button className="btn sm" onClick={() => navigate(`/orders/${generateResult.orderId}`)}>
                View Order
              </button>
            )}
            <button className="btn sm" onClick={() => navigate('/queue')}>View Queue</button>
          </div>
        )}

        {/* Error banner */}
        {generateError && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
            borderRadius: 6, padding: '10px 14px',
          }}>
            <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
            <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{generateError}</span>
            <button
              className="btn sm"
              onClick={handleGenerate}
              disabled={generating || saving}
            >
              Retry
            </button>
          </div>
        )}

        {/* Footer actions */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 'auto', paddingTop: 4 }}>
          <button className="btn sm" onClick={() => navigate('/projects')} disabled={saving || generating}>
            Cancel
          </button>
          <button className="btn sm" onClick={handleSave} disabled={!canSave}>
            {saving && !generating ? 'Saving…' : 'Save'}
          </button>
          <button
            className="btn primary sm"
            onClick={handleGenerate}
            disabled={!canSave || items.length === 0}
          >
            {generating ? 'Generating…' : 'Generate'}
          </button>
        </div>
      </div>
    </div>
  );
}
