import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentRequirementPicker } from '../components/FilamentRequirementPicker';
import type { FilamentRequirement } from '../components/FilamentRequirementPicker';
import { PrinterEligibilityPicker } from '../components/PrinterEligibilityPicker';
import { useFiles } from '../api/files';
import { useSpoolmanConfig, useFilaments } from '../api/spoolman';
import type { LibraryFile, FolderNode } from '../data/types';
import {
  getProject, createProject, patchProject,
  addProjectItem, updateProjectItem,
  addProjectLink, updateProjectLink, deleteProjectLink,
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
        <span style={{ color: isActive ? 'var(--accent-hi)' : 'var(--text-3)' }}>{Icons.files}</span>
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
  filament_type: string;
  filament_color: string;
  filament_id: number | null;
  sort_order: number;
}

interface LocalLink {
  localId: string;
  serverId?: number;
  url: string;
  label: string;
}

let _lid = 0;
const newLocalId = () => String(++_lid);

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

  // Spoolman integration
  const { config: spoolmanConfig } = useSpoolmanConfig();
  const spoolmanEnabled = spoolmanConfig?.enabled ?? false;
  const spoolmanFilaments = useFilaments(spoolmanEnabled);

  // Project header fields
  const [name, setName] = useState('');
  const [customer, setCustomer] = useState('');
  const [orderType, setOrderType] = useState<'internal' | 'customer'>('internal');
  const [onHold, setOnHold] = useState(false);
  const [dueDate, setDueDate] = useState('');
  const [notes, setNotes] = useState('');

  // Part items
  const [items, setItems] = useState<LocalItem[]>([]);
  const [serverItems, setServerItems] = useState<Map<number, ProjectItem>>(new Map());
  // Links
  const [links, setLinks] = useState<LocalLink[]>([]);
  const [deletedLinkIds, setDeletedLinkIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number } | null>(null);
  const [showPrinterPicker, setShowPrinterPicker] = useState(false);
  const [eligiblePrinterIds, setEligiblePrinterIds] = useState<number[]>([]);

  // Dirty tracking
  const [cleanSnap, setCleanSnap] = useState('');
  const [saveOk, setSaveOk] = useState(false);

  function computeSnap(
    n: string, c: string, ot: string, oh: boolean, dd: string, no: string,
    its: LocalItem[], lks: LocalLink[],
  ) {
    return JSON.stringify({
      name: n, customer: ot === 'customer' ? c : '', orderType: ot,
      onHold: oh, dueDate: dd, notes: no,
      items: its.map(i => ({
        fid: i.file_id, qty: i.quantity,
        ft: i.filament_type, fc: i.filament_color, fi: i.filament_id, so: i.sort_order,
      })),
      links: lks.map(l => ({ url: l.url, label: l.label, sid: l.serverId })),
    });
  }

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
      const n = p.name;
      const c = p.customer ?? '';
      const ot = (p.order_type as 'internal' | 'customer') ?? 'internal';
      const oh = p.on_hold ?? false;
      const dd = p.due_date ?? '';
      const no = p.notes ?? '';
      const its: LocalItem[] = p.items.map(it => ({
        localId: newLocalId(),
        serverId: it.id,
        file_id: it.file_id,
        file_name: it.file_name,
        quantity: it.quantity,
        filament_type: it.filament_type,
        filament_color: it.filament_color,
        filament_id: it.filament_id,
        sort_order: it.sort_order,
      }));
      const lks: LocalLink[] = (p.links ?? []).map(l => ({
        localId: newLocalId(),
        serverId: l.id,
        url: l.url,
        label: l.label ?? '',
      }));
      setName(n); setCustomer(c); setOrderType(ot); setOnHold(oh);
      setDueDate(dd); setNotes(no); setItems(its); setLinks(lks);
      setDeletedLinkIds([]);
      setServerItems(new Map(p.items.map(it => [it.id, it])));
      setCleanSnap(computeSnap(n, c, ot, oh, dd, no, its, lks));
    }).catch(console.error);
  }, [projectId]);

  function addFile(f: LibraryFile) {
    setItems(prev => [
      ...prev,
      {
        localId: newLocalId(),
        file_id: f.id,
        file_name: f.original_filename,
        quantity: 1,
        filament_type: 'any',
        filament_color: 'any',
        filament_id: null,
        sort_order: prev.length,
      },
    ]);
  }

  function updateItem(localId: string, patch: Partial<LocalItem>) {
    setItems(prev => prev.map(it => it.localId === localId ? { ...it, ...patch } : it));
  }

  function removeItem(localId: string) {
    setItems(prev => prev.filter(it => it.localId !== localId));
  }

  function setItemFilament(localId: string, req: FilamentRequirement) {
    updateItem(localId, {
      filament_type: req.filament_type,
      filament_color: req.filament_color,
      filament_id: req.filament_id,
    });
  }

  async function saveProject(): Promise<number> {
    const projectFields = {
      name,
      customer: orderType === 'customer' ? customer : '',
      order_type: orderType,
      on_hold: onHold,
      due_date: dueDate || null,
      notes: notes || null,
    };
    if (projectId) {
      await patchProject(projectId, projectFields);
      const existing = items.filter(i => i.serverId);
      const newOnes = items.filter(i => !i.serverId);
      for (const it of existing) {
        await updateProjectItem(projectId, it.serverId!, {
          quantity: it.quantity,
          filament_type: it.filament_type,
          filament_color: it.filament_color,
          filament_id: it.filament_id,
          sort_order: it.sort_order,
        });
      }
      for (let i = 0; i < newOnes.length; i++) {
        const it = newOnes[i];
        await addProjectItem(projectId, {
          file_id: it.file_id,
          quantity: it.quantity,
          filament_type: it.filament_type,
          filament_color: it.filament_color,
          filament_id: it.filament_id,
          sort_order: it.sort_order,
        });
      }
      for (const id of deletedLinkIds) {
        await deleteProjectLink(projectId, id);
      }
      for (const lk of links) {
        if (lk.serverId) {
          await updateProjectLink(projectId, lk.serverId, { url: lk.url, label: lk.label || null });
        } else {
          await addProjectLink(projectId, { url: lk.url, label: lk.label || null });
        }
      }
      return projectId;
    } else {
      const proj = await createProject(projectFields);
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        await addProjectItem(proj.id, {
          file_id: it.file_id,
          quantity: it.quantity,
          filament_type: it.filament_type,
          filament_color: it.filament_color,
          filament_id: it.filament_id,
          sort_order: i,
        });
      }
      for (const lk of links) {
        await addProjectLink(proj.id, { url: lk.url, label: lk.label || null });
      }
      return proj.id;
    }
  }

  async function handleSave() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const pid = await saveProject();
      setDeletedLinkIds([]);
      setCleanSnap(computeSnap(name, customer, orderType, onHold, dueDate, notes, items, links));
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
      if (!projectId) navigate(`/projects/${pid}`, { replace: true });
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  async function handleGenerate(printerIds: number[]) {
    if (!name.trim() || items.length === 0) return;
    setSaving(true);
    setGenerateError('');
    setGenerateResult(null);
    setShowPrinterPicker(false);
    try {
      const pid = await saveProject();
      if (!projectId) navigate(`/projects/${pid}`, { replace: true });
      setGenerating(true);
      const result = await generateProject(pid, printerIds);
      setGenerateResult({ jobCount: result.jobs.length });
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

  const currentSnap = computeSnap(name, customer, orderType, onHold, dueDate, notes, items, links);
  const isDirty = projectId ? (cleanSnap !== '' && currentSnap !== cleanSnap) : true;
  const canSave = name.trim().length > 0 && !saving && !generating;
  const showSave = isDirty && canSave;

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
                width: '100%', padding: '5px 10px',
                background: 'transparent', border: 'none',
                color: 'var(--text-2)', cursor: 'pointer',
                textAlign: 'left', fontSize: 12,
                display: 'flex', alignItems: 'center', gap: 6, borderRadius: 4,
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
      <div className="card" style={{ padding: 20, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Project metadata fields */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label" htmlFor="proj-name">
            Project name *
            {projectId && (
              <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-4)', marginLeft: 6 }}>
                #{projectId}
              </span>
            )}
          </label>
            <input
              id="proj-name"
              className="input"
              placeholder="e.g. Gridfinity Tray Set"
              value={name}
              onChange={e => setName(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Type</label>
            <select
              className="select"
              value={orderType}
              onChange={e => setOrderType(e.target.value as 'internal' | 'customer')}
            >
              <option value="internal">Internal</option>
              <option value="customer">Customer</option>
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Due date</label>
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <input
                type="date"
                className="input"
                value={dueDate}
                onChange={e => setDueDate(e.target.value)}
                style={{ flex: 1 }}
              />
              {dueDate && (
                <button
                  className="btn ghost icon sm"
                  onClick={() => setDueDate('')}
                  title="Clear due date"
                  style={{ flexShrink: 0 }}
                >
                  {Icons.x}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Customer + notes row */}
        <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
          {orderType === 'customer' && (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label className="label">Customer</label>
              <input
                className="input"
                placeholder="Customer name"
                value={customer}
                onChange={e => setCustomer(e.target.value)}
              />
            </div>
          )}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Notes</label>
            <input
              className="input"
              placeholder="Optional notes"
              value={notes}
              onChange={e => setNotes(e.target.value)}
            />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                          fontSize: 13, color: onHold ? 'var(--warn)' : 'var(--text-2)',
                          paddingBottom: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={onHold}
              onChange={e => setOnHold(e.target.checked)}
            />
            On hold
          </label>
        </div>

        {/* Links editor */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Links ({links.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => setLinks(prev => [...prev, { localId: newLocalId(), url: '', label: '' }])}
            >
              + Add link
            </button>
          </div>
          {links.map(lk => (
            <div key={lk.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input
                className="input"
                placeholder="https://..."
                value={lk.url}
                onChange={e => setLinks(prev => prev.map(l => l.localId === lk.localId ? { ...l, url: e.target.value } : l))}
                style={{ flex: 2 }}
              />
              <input
                className="input"
                placeholder="Label (optional)"
                value={lk.label}
                onChange={e => setLinks(prev => prev.map(l => l.localId === lk.localId ? { ...l, label: e.target.value } : l))}
                style={{ flex: 1 }}
              />
              <button
                className="btn ghost icon sm"
                onClick={() => {
                  if (lk.serverId) setDeletedLinkIds(prev => [...prev, lk.serverId!]);
                  setLinks(prev => prev.filter(l => l.localId !== lk.localId));
                }}
                title="Remove link"
              >
                {Icons.x}
              </button>
            </div>
          ))}
        </div>

        {/* Part items table */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Parts ({items.length})
            </span>
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
                gridTemplateColumns: '1fr 64px 260px auto',
                gap: 8, padding: '0 4px',
                fontSize: 11, fontWeight: 500, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: '0.05em',
              }}>
                <span>File</span>
                <span>Qty</span>
                <span>Filament</span>
                <span />
              </div>
              {items.map((it) => (
                <div key={it.localId} style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 64px 260px auto',
                  gap: 8, alignItems: 'start',
                }}>
                  {/* File name + progress */}
                  <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, paddingTop: 4 }}>
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

                  {/* Quantity */}
                  <input
                    type="number"
                    className="input"
                    min={1} max={99}
                    value={it.quantity}
                    onChange={e => updateItem(it.localId, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                    style={{ textAlign: 'center' }}
                  />

                  {/* Filament requirement */}
                  <FilamentRequirementPicker
                    value={{ filament_type: it.filament_type, filament_color: it.filament_color, filament_id: it.filament_id }}
                    onChange={req => setItemFilament(it.localId, req)}
                    spoolmanFilaments={spoolmanFilaments}
                    spoolmanEnabled={spoolmanEnabled}
                  />

                  {/* Remove */}
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

        {/* Printer picker (shown when Generate… is clicked) */}
        {showPrinterPicker && !generating && !saving && (
          <div style={{
            border: '1px solid var(--border)', borderRadius: 8,
            padding: '14px 16px', background: 'var(--bg-2)',
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)',
                          textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
              Eligible printers
            </div>
            <PrinterEligibilityPicker selected={eligiblePrinterIds} onChange={setEligiblePrinterIds} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn sm" onClick={() => setShowPrinterPicker(false)}>Cancel</button>
              <button
                className="btn primary sm"
                onClick={() => handleGenerate(eligiblePrinterIds)}
                disabled={!canSave || items.length === 0}
              >
                Generate
              </button>
            </div>
          </div>
        )}

        {/* Generation success */}
        {generateResult && (
          <div style={{
            border: '1px solid var(--ok)', borderRadius: 8,
            padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
            display: 'flex', alignItems: 'center', gap: 12,
          }}>
            <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
            <span style={{ fontSize: 14, flex: 1 }}>
              {generateResult.jobCount} job{generateResult.jobCount !== 1 ? 's' : ''} added to queue
            </span>
            <button className="btn sm" onClick={() => navigate('/queue')}>Queue</button>
            {projectId && (
              <button className="btn sm" onClick={() => navigate(`/projects/${projectId}`)}>
                Details
              </button>
            )}
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
            <button className="btn sm" onClick={() => handleGenerate(eligiblePrinterIds)} disabled={generating || saving}>Retry</button>
          </div>
        )}

        {/* Footer actions */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 'auto', paddingTop: 4 }}>
          <button className="btn sm" onClick={() => navigate(projectId ? `/projects/${projectId}` : '/projects')} disabled={saving || generating}>
            Cancel
          </button>
          {showSave && (
            <button className="btn sm" onClick={handleSave} disabled={saving}>
              {saving && !generating ? 'Saving…' : (projectId ? 'Save changes' : 'Save')}
            </button>
          )}
          <button
            className="btn primary sm"
            onClick={() => { setShowPrinterPicker(v => !v); setGenerateError(''); setGenerateResult(null); }}
            disabled={!canSave || items.length === 0}
          >
            {generating ? 'Generating…' : 'Generate…'}
          </button>
        </div>
      </div>

      {/* Saved toast */}
      {saveOk && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
          background: 'var(--ok)', color: '#fff',
          padding: '10px 18px', borderRadius: 8,
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 14, fontWeight: 500,
          boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
          pointerEvents: 'none',
        }}>
          {Icons.check} Saved
        </div>
      )}
    </div>
  );
}
