import { useState, useEffect, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentAskEditor } from '../components/FilamentAskEditor';
import { NO_ASK, type FilamentAsk } from '../lib/filamentAsk';
import { buildStlTree, isPickableStl, stlFilesInView } from '../lib/stlTree';
import {
  askOf, draftSnapshot, newLocalId, parseGenerateError, projectToDraft,
  type LocalItem, type LocalLink, type LocalPart, type OrderType, type ProjectDraft, type RemovedSnapshot,
} from '../lib/projectDraft';
import { StlFolderTree } from '../components/StlFolderTree';
import { PrinterEligibilityPicker } from '../components/PrinterEligibilityPicker';
import { ProcessPresetPicker } from '../components/ProcessPresetPicker';
import { CustomerPicker } from '../components/CustomerPicker';
import { useFiles } from '../api/files';
import { useInventory, useMaterials } from '../api/inventory';
import type { LibraryFile } from '../data/types';
import { getProject, generateProject, type ProjectItem } from '../api/projects';
import { syncProject } from '../api/projectSync';

const UNDO_TIMEOUT_MS = 6000;

// ---------------------------------------------------------------------------
// ProjectBuilderScreen
// ---------------------------------------------------------------------------

export function ProjectBuilderScreen() {
  const { id } = useParams<{ id: string }>();
  // "New project" from a customer's page links here with ?customer=<id>; used on create only.
  const [searchParams] = useSearchParams();
  const newForCustomerId = id ? null : Number(searchParams.get('customer')) || null;
  const projectId = id ? parseInt(id) : null;
  const navigate = useNavigate();

  // Inventory catalog (materials), when a provider is active
  const inventory = useInventory();
  const catalogEnabled = !!inventory.plugin;
  const materials = useMaterials(catalogEnabled);

  // Project header fields
  const [name, setName] = useState('');
  const [customer, setCustomer] = useState('');
  const [customerId, setCustomerId] = useState<number | null>(newForCustomerId);
  const [orderType, setOrderType] = useState<OrderType>(newForCustomerId ? 'customer' : 'internal');
  const [onHold, setOnHold] = useState(false);
  const [dueDate, setDueDate] = useState('');
  const [notes, setNotes] = useState('');
  const [price, setPrice] = useState('');
  // Loaded, not edited here: a draft (customer request) can't generate jobs until promoted.
  const [isDraft, setIsDraft] = useState(false);

  // Part items
  const [items, setItems] = useState<LocalItem[]>([]);
  const [serverItems, setServerItems] = useState<Map<number, ProjectItem>>(new Map());
  const [deletedItemIds, setDeletedItemIds] = useState<number[]>([]);
  // Links
  const [links, setLinks] = useState<LocalLink[]>([]);
  const [deletedLinkIds, setDeletedLinkIds] = useState<number[]>([]);
  // Non-printed parts (hardware, e.g. magnets/screws)
  const [parts, setParts] = useState<LocalPart[]>([]);
  const [deletedPartIds, setDeletedPartIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number } | null>(null);
  const [showPrinterPicker, setShowPrinterPicker] = useState(false);
  const [eligiblePrinterIds, setEligiblePrinterIds] = useState<number[]>([]);
  const [processPreset, setProcessPreset] = useState<string | null>(null);

  // Undo toast for accidental removals (items, links, non-printed parts)
  const [lastRemoved, setLastRemoved] = useState<RemovedSnapshot | null>(null);
  const undoTimerRef = useRef<number | null>(null);
  function scheduleUndoClear() {
    if (undoTimerRef.current) window.clearTimeout(undoTimerRef.current);
    undoTimerRef.current = window.setTimeout(() => setLastRemoved(null), UNDO_TIMEOUT_MS);
  }
  function undoRemove() {
    if (!lastRemoved) return;
    if (undoTimerRef.current) window.clearTimeout(undoTimerRef.current);
    if (lastRemoved.kind === 'item') {
      setItems(prev => {
        const next = [...prev];
        next.splice(lastRemoved.index, 0, lastRemoved.item);
        return next;
      });
      if (lastRemoved.item.serverId) {
        setDeletedItemIds(prev => prev.filter(id => id !== lastRemoved.item.serverId));
      }
    } else if (lastRemoved.kind === 'link') {
      setLinks(prev => {
        const next = [...prev];
        next.splice(lastRemoved.index, 0, lastRemoved.link);
        return next;
      });
      if (lastRemoved.link.serverId) {
        setDeletedLinkIds(prev => prev.filter(id => id !== lastRemoved.link.serverId));
      }
    } else {
      setParts(prev => {
        const next = [...prev];
        next.splice(lastRemoved.index, 0, lastRemoved.part);
        return next;
      });
      if (lastRemoved.part.serverId) {
        setDeletedPartIds(prev => prev.filter(id => id !== lastRemoved.part.serverId));
      }
    }
    setLastRemoved(null);
  }

  // Dirty tracking
  const [cleanSnap, setCleanSnap] = useState('');
  const [saveOk, setSaveOk] = useState(false);

  // File tree state
  const { files: allFiles } = useFiles({});
  const stlFiles = allFiles.filter(isPickableStl);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const searchLower = search.trim().toLowerCase();
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = stlFilesInView(stlFiles, selectedFolder, search);

  // Load existing project
  useEffect(() => {
    if (!projectId) return;
    getProject(projectId).then(p => {
      const d = projectToDraft(p);
      setName(d.name); setCustomer(d.customer); setCustomerId(d.customerId); setOrderType(d.orderType); setOnHold(d.onHold);
      setDueDate(d.dueDate); setNotes(d.notes); setPrice(d.price); setIsDraft(p.stage === 'draft');
      setItems(d.items); setLinks(d.links); setParts(d.parts);
      setDeletedItemIds([]);
      setDeletedLinkIds([]);
      setDeletedPartIds([]);
      setServerItems(new Map(p.items.map(it => [it.id, it])));
      setCleanSnap(draftSnapshot(d));
    }).catch(console.error);
  }, [projectId]);

  function addFile(f: LibraryFile) {
    setItems(prev => {
      const existing = prev.find(it => it.file_id === f.id);
      if (existing) {
        return prev.map(it =>
          it.localId === existing.localId ? { ...it, quantity: it.quantity + 1 } : it,
        );
      }
      return [
        ...prev,
        {
          localId: newLocalId(),
          file_id: f.id,
          file_name: f.original_filename,
          quantity: 1,
          ...NO_ASK,
          sort_order: prev.length,
        },
      ];
    });
  }

  function updateItem(localId: string, patch: Partial<LocalItem>) {
    setItems(prev => prev.map(it => it.localId === localId ? { ...it, ...patch } : it));
  }

  function removeItem(localId: string) {
    const index = items.findIndex(it => it.localId === localId);
    if (index === -1) return;
    const item = items[index];
    if (item.serverId) setDeletedItemIds(prev => [...prev, item.serverId!]);
    setLastRemoved({ kind: 'item', label: item.file_name, index, item });
    setItems(prev => prev.filter(it => it.localId !== localId));
    scheduleUndoClear();
  }

  function removeLink(localId: string) {
    const index = links.findIndex(l => l.localId === localId);
    if (index === -1) return;
    const link = links[index];
    if (link.serverId) setDeletedLinkIds(prev => [...prev, link.serverId!]);
    setLastRemoved({ kind: 'link', label: link.label || link.url || 'link', index, link });
    setLinks(prev => prev.filter(l => l.localId !== localId));
    scheduleUndoClear();
  }

  function removePart(localId: string) {
    const index = parts.findIndex(p => p.localId === localId);
    if (index === -1) return;
    const part = parts[index];
    if (part.serverId) setDeletedPartIds(prev => [...prev, part.serverId!]);
    setLastRemoved({ kind: 'part', label: part.name || 'part', index, part });
    setParts(prev => prev.filter(p => p.localId !== localId));
    scheduleUndoClear();
  }

  function setItemFilament(localId: string, req: FilamentAsk) {
    updateItem(localId, askOf(req));
  }

  function applyFilamentToAll(req: FilamentAsk) {
    setItems(prev => prev.map(it => ({ ...it, ...askOf(req) })));
  }

  const draft: ProjectDraft = { name, customer, customerId, orderType, onHold, dueDate, notes, price, items, links, parts };
  const currentSnap = draftSnapshot(draft);

  /**
   * Save the draft; returns the project id and the snapshot of what was saved (the new clean state). Rows are
   * stamped with their server ids, and dropped from the pending deletes, as each request succeeds — so a retry
   * after a partial failure picks up where it stopped.
   */
  async function saveProject(): Promise<{ pid: number; savedSnap: string }> {
    const ids = new Map<string, number>();
    const stamp = <R extends { localId: string; serverId?: number }>(rows: R[]) =>
      rows.map(r => (ids.has(r.localId) ? { ...r, serverId: ids.get(r.localId) } : r));
    const pid = await syncProject(projectId, draft, { itemIds: deletedItemIds, linkIds: deletedLinkIds, partIds: deletedPartIds }, e => {
      if (e.type === 'created') {
        ids.set(e.localId, e.id);
        const set = (r: { localId: string }) => r.localId === e.localId;
        if (e.kind === 'item') setItems(prev => prev.map(r => (set(r) ? { ...r, serverId: e.id } : r)));
        else if (e.kind === 'link') setLinks(prev => prev.map(r => (set(r) ? { ...r, serverId: e.id } : r)));
        else setParts(prev => prev.map(r => (set(r) ? { ...r, serverId: e.id } : r)));
      } else {
        const drop = (prev: number[]) => prev.filter(id => id !== e.id);
        if (e.kind === 'item') setDeletedItemIds(drop);
        else if (e.kind === 'link') setDeletedLinkIds(drop);
        else setDeletedPartIds(drop);
      }
    });
    return { pid, savedSnap: draftSnapshot({ ...draft, items: stamp(items), links: stamp(links), parts: stamp(parts) }) };
  }

  async function handleSave() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const { pid, savedSnap } = await saveProject();
      setCleanSnap(savedSnap);
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
      const { pid } = await saveProject();
      // Stay in the builder (its /edit route keeps this component mounted) so the generate result or
      // error is shown here and a retry reuses the saved project instead of creating a second one.
      if (!projectId) navigate(`/projects/${pid}/edit`, { replace: true });
      setGenerating(true);
      const result = await generateProject(pid, printerIds, processPreset);
      setGenerateResult({ jobCount: result.jobs.length });
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

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
        <input
          className="input"
          placeholder="Search files…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: 6, fontSize: 12 }}
        />
        {!searchLower && (
          <StlFolderTree node={folderTree} depth={0} selected={selectedFolder} onSelect={setSelectedFolder} />
        )}
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 6 }}>
          {folderFiles.length === 0 && (
            <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text-4)' }}>
              {searchLower ? 'No matching STL files' : 'No STL files here'}
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
              <CustomerPicker
                value={{ customerId, customerText: customer }}
                onChange={v => { setCustomerId(v.customerId); setCustomer(v.customerText); }}
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

        {/* Payment row */}
        <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label" htmlFor="proj-price">Price</label>
            <input
              id="proj-price"
              type="number" min="0" step="0.01"
              className="input"
              placeholder="Quoted total"
              value={price}
              onChange={e => setPrice(e.target.value)}
            />
          </div>
          <div className="small muted" style={{ flex: 2, paddingBottom: 6 }}>
            {projectId
              ? 'Payments received are recorded on the project page — amount paid and payment status follow from them.'
              : 'Record payments on the project page once it is saved.'}
          </div>
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
                onClick={() => removeLink(lk.localId)}
                title="Remove link"
              >
                {Icons.x}
              </button>
            </div>
          ))}
        </div>

        {/* Non-printed parts editor (hardware, e.g. magnets/screws) */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>
              Non-printed parts ({parts.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => setParts(prev => [
                ...prev, { localId: newLocalId(), name: '', quantity: 1, allocated: false, unitCost: '' },
              ])}
            >
              + Add part
            </button>
          </div>
          {parts.map(pt => (
            <div key={pt.localId} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input
                className="input"
                placeholder="e.g. 3mm magnet"
                value={pt.name}
                onChange={e => setParts(prev => prev.map(p => p.localId === pt.localId ? { ...p, name: e.target.value } : p))}
                style={{ flex: 2 }}
              />
              <input
                type="number"
                className="input"
                min={1}
                value={pt.quantity}
                onChange={e => setParts(prev => prev.map(p => p.localId === pt.localId ? { ...p, quantity: Math.max(1, parseInt(e.target.value) || 1) } : p))}
                style={{ width: 64, textAlign: 'center' }}
              />
              <input
                type="number"
                className="input"
                min={0}
                step="0.01"
                placeholder="$ each"
                aria-label="Unit cost"
                value={pt.unitCost}
                onChange={e => setParts(prev => prev.map(p => p.localId === pt.localId ? { ...p, unitCost: e.target.value } : p))}
                style={{ width: 84 }}
              />
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                              fontSize: 12, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                <input
                  type="checkbox"
                  checked={pt.allocated}
                  onChange={e => setParts(prev => prev.map(p => p.localId === pt.localId ? { ...p, allocated: e.target.checked } : p))}
                />
                Allocated
              </label>
              <button
                className="btn ghost icon sm"
                onClick={() => removePart(pt.localId)}
                title="Remove part"
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
                gridTemplateColumns: '1fr 64px 340px auto',
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
                  gridTemplateColumns: '1fr 64px 340px auto',
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
                  <FilamentAskEditor
                    id={`item-ask-${it.localId}`}
                    value={askOf(it)}
                    onChange={req => setItemFilament(it.localId, req)}
                    materials={materials}
                    catalogEnabled={catalogEnabled}
                  />

                  {/* Apply to all + Remove */}
                  <span style={{ display: 'flex', gap: 4 }}>
                    {items.length > 1 && (
                      <button
                        className="btn ghost icon sm"
                        onClick={() => applyFilamentToAll(askOf(it))}
                        title="Apply this filament to every part"
                      >
                        {Icons.copy}
                      </button>
                    )}
                    <button
                      className="btn ghost icon sm"
                      onClick={() => removeItem(it.localId)}
                      title="Remove"
                    >
                      {Icons.x}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Printer picker (shown when Create… is clicked) */}
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
            <div style={{ marginTop: 12 }}>
              <ProcessPresetPicker printerIds={eligiblePrinterIds} value={processPreset} onChange={setProcessPreset} />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn sm" onClick={() => setShowPrinterPicker(false)}>Cancel</button>
              <button
                className={eligiblePrinterIds.length === 0 ? 'btn sm' : 'btn primary sm'}
                onClick={() => handleGenerate(eligiblePrinterIds)}
                disabled={!canSave || items.length === 0}
                title={eligiblePrinterIds.length === 0
                  ? 'No printers selected — jobs will be created but not dispatched'
                  : undefined}
              >
                {eligiblePrinterIds.length === 0 ? 'Create without dispatch' : 'Create'}
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
            disabled={!canSave || items.length === 0 || isDraft}
            title={isDraft ? 'Promote to planning before creating jobs' : undefined}
          >
            {generating ? 'Creating…' : 'Create…'}
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

      {/* Undo-removal toast */}
      {lastRemoved && (
        <div style={{
          position: 'fixed', bottom: 24, left: 24, zIndex: 1000,
          background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text-1)',
          padding: '10px 12px 10px 16px', borderRadius: 8,
          display: 'flex', alignItems: 'center', gap: 10,
          fontSize: 13, boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
        }}>
          <span>Removed {lastRemoved.label || lastRemoved.kind}</span>
          <button className="btn sm" onClick={undoRemove}>Undo</button>
        </div>
      )}
    </div>
  );
}
