import { ItemSourcePicker } from '../components/ItemSourcePicker';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentAskEditor } from '../components/FilamentAskEditor';
import { NO_ASK, type FilamentAsk } from '../lib/filamentAsk';
import { buildStlTree, filesInView, isPickableModel } from '../lib/stlTree';
import { askOf, parseGenerateError, type OrderType } from '../lib/projectDraft';
import { currentSnapshot, type HeaderFields } from '../lib/projectBuilderState';
import { useAutoDismiss } from '../components/useAutoDismiss';
import { useProjectDraft } from '../components/builder/useProjectDraft';
import { StlFolderTree } from '../components/StlFolderTree';
import { PrintGroupsCard } from '../components/PrintGroupsCard';
import { GroupBanner, type BannerSelection } from '../components/builder/GroupBanner';
import { ItemSections, type BulkAction } from '../components/builder/ItemSections';
import { BuilderTabs, ReviewPanel, type BuilderTab } from '../components/builder/ReviewPanel';
import { CustomerPicker } from '../components/CustomerPicker';
import { FileDropZone } from '../components/FileDropZone';
import { promoteProject } from '../api/customers';
import { useFiles } from '../api/files';
import { useInventory, useMaterials } from '../api/inventory';
import type { LibraryFile } from '../data/types';
import {
  listProjectGroups, createProjectGroup, orderProjectGroups, getProjectReview, generateProject,
  type ProjectGroup, type ProjectReview,
} from '../api/projects';

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

  // Section tabs (BIZ-363): one component, one state, so nothing is lost moving between them.
  const [tab, setTab] = useState<BuilderTab>(projectId ? 'items' : 'details');

  const { state, dispatch, isDirty, save } = useProjectDraft(projectId, newForCustomerId);
  const { draft, lastRemoved, serverItems, isDraftStage: isDraft } = state;
  const { name, customer, customerId, orderType, onHold, dueDate, notes, price, items, links, parts } = draft;
  const setFields = (fields: Partial<HeaderFields>) => dispatch({ type: 'setFields', fields });

  // Print groups (BIZ-360) and the banner selection
  const [groups, setGroups] = useState<ProjectGroup[]>([]);
  const [bannerSel, setBannerSel] = useState<BannerSelection>('all');
  const [groupsTick, setGroupsTick] = useState(0);
  const [bulkAsk, setBulkAsk] = useState<{ ids: string[]; value: FilamentAsk } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number; warnings: string[] } | null>(null);
  // Review (BIZ-363)
  const [review, setReview] = useState<ProjectReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const [saveOk, setSaveOk] = useState(false);
  useAutoDismiss(saveOk, () => setSaveOk(false), 2000);

  // File tree state
  const { files: allFiles } = useFiles({});
  // Every model and pre-sliced file can be an item (BIZ-356): STLs, 3MF plates/objects, gcode plates.
  const stlFiles = allFiles.filter(isPickableModel);
  const fileById = new Map(allFiles.map(f => [f.id, f]));
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const searchLower = search.trim().toLowerCase();
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = filesInView(stlFiles, selectedFolder, search);

  // Print groups of a saved project, in queue order (the Default Group is created lazily by the list).
  useEffect(() => {
    if (!projectId) { setGroups([]); return; }
    let alive = true;
    listProjectGroups(projectId).then(gs => {
      if (!alive) return;
      const list = Array.isArray(gs) ? gs : [];
      setGroups(list);
      // A deleted group's items are the Default Group's now (the server moved them): never save a dead group id.
      const ids = new Set(list.map(g => g.id));
      dispatch({ type: 'groupsListed', groupIds: [...ids] });
      setBannerSel(sel => (sel === 'all' || ids.has(sel) ? sel : 'all'));
    }).catch(() => {});
    return () => { alive = false; };
  }, [projectId, groupsTick, dispatch]);   // dispatch is stable
  const defaultGroupId = groups.find(g => g.is_default)?.id ?? null;
  // The Groups tab has no "All": it always edits one group.
  useEffect(() => {
    if (tab === 'groups' && (bannerSel === 'all' || !groups.some(g => g.id === bannerSel)) && groups.length) {
      setBannerSel(defaultGroupId ?? groups[0].id);
    }
  }, [tab, bannerSel, groups, defaultGroupId]);

  /** New rows join the badge being shown (BIZ-363), else the Default Group. */
  const addFile = (file: LibraryFile) =>
    dispatch({ type: 'addFile', file, groupId: typeof bannerSel === 'number' ? bannerSel : null, defaultGroupId });

  async function newGroup(moveIds: string[] = []) {
    if (!projectId) return;
    const taken = new Set(groups.map(g => g.name));
    let n = groups.length;
    while (taken.has(`Group ${n}`)) n++;
    try {
      const g = await createProjectGroup(projectId, `Group ${n}`);
      if (moveIds.length) dispatch({ type: 'moveItems', localIds: moveIds, groupId: g.id });
      setBannerSel(g.id);
      setGroupsTick(t => t + 1);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  function onBulk(ids: string[], action: BulkAction) {
    if (action.kind === 'group') dispatch({ type: 'moveItems', localIds: ids, groupId: action.groupId });
    else if (action.kind === 'rule') dispatch({ type: 'setItemsRule', localIds: ids, rule: action.rule });
    else if (action.kind === 'qty') dispatch({ type: 'setItemsQuantity', localIds: ids, quantity: action.quantity });
    else if (action.kind === 'remove') dispatch({ type: 'removeItems', localIds: ids });
    else {
      const first = items.find(it => ids.includes(it.localId));
      setBulkAsk({ ids, value: first ? askOf(first) : { ...NO_ASK } });
    }
  }

  /** Save draft: stays in the Builder (a new project moves to its /edit route, so later saves update it). */
  async function handleSave(): Promise<number | null> {
    if (!name.trim()) return null;
    setSaving(true);
    setSaveError('');
    try {
      const pid = await save();
      setSaveOk(true);
      if (!projectId) navigate(`/projects/${pid}/edit`, { replace: true });
      return pid;
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setSaving(false);
    }
  }

  const [reviewedSnap, setReviewedSnap] = useState<string | null>(null);
  const loadReview = useCallback(async (pid: number, snap: string) => {
    setReviewLoading(true);
    setReviewError(null);
    try {
      setReview(await getProjectReview(pid));
      setReviewedSnap(snap);
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : String(e));   // shown instead of the checks; Queue stays disabled
    } finally { setReviewLoading(false); }
  }, []);

  /** Queue project: save, then generate every group with its own printers (BIZ-360); the Review gate keeps this for 0 errors. */
  async function handleQueue() {
    if (!name.trim() || items.length === 0) return;
    setSaving(true);
    setGenerateError('');
    setGenerateResult(null);
    try {
      const pid = await save();
      setGenerating(true);
      const result = await generateProject(pid);
      setGenerateResult({ jobCount: result.jobs.length, warnings: (result.groups ?? []).map(g => g.warning).filter((w): w is string => !!w) });
      loadReview(pid, currentSnap);
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

  const currentSnap = currentSnapshot(state);
  const canSave = name.trim().length > 0 && !saving && !generating;

  // Entering Review saves what changed (the checks read the saved project), then runs the checks.
  const reviewFor = useRef('');
  useEffect(() => {
    if (tab !== 'review' || !projectId || saving) return;
    const key = `${projectId}:${currentSnap}:${groupsTick}`;
    if (reviewFor.current === key) return;
    reviewFor.current = key;
    const snap = currentSnap;
    (async () => {
      if (isDirty && canSave && (await handleSave()) === null) {
        setReview(null);                 // the checks would describe what isn't saved
        setReviewedSnap(null);
        return;
      }
      loadReview(projectId, snap);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, projectId, currentSnap, groupsTick]);

  const ORDER: BuilderTab[] = ['details', 'items', 'groups', 'review'];
  const at = ORDER.indexOf(tab);
  const counts = new Map<number | 'all', { items: number; parts: number }>([['all', { items: items.length, parts: 0 }]]);
  for (const it of items) {
    const g = it.group_id ?? defaultGroupId;
    if (g == null) continue;
    const c = counts.get(g) ?? { items: 0, parts: 0 };
    counts.set(g, { items: c.items + 1, parts: c.parts + it.quantity });
  }
  const banner = (showAll: boolean, editing: boolean) => (
    <GroupBanner groups={groups} counts={counts} selected={bannerSel} onSelect={setBannerSel} showAll={showAll}
                 onDropItems={editing ? undefined : (ids, target) => {
                   if (target === 'new') newGroup(ids);
                   else dispatch({ type: 'moveItems', localIds: ids, groupId: target });
                 }}
                 onNewGroup={() => newGroup()}
                 onReorder={editing && projectId ? async ids => {
                   try { setGroups(await orderProjectGroups(projectId, ids)); } catch { setGroupsTick(t => t + 1); }
                 } : undefined}
                 newGroupDisabled={projectId ? undefined : 'Save the draft first to add groups'} />
  );
  const needsSave = (what: string) => (
    <div className="card" style={{ padding: 20, textAlign: 'center' }}>
      <div className="small muted" style={{ marginBottom: 10 }}>Save the draft to {what}.</div>
      <button className="btn primary sm" onClick={handleSave} disabled={!canSave}>Save draft</button>
    </div>
  );

  async function promote() {
    if (!projectId) return;
    try {
      await promoteProject(projectId, 'planning');
      dispatch({ type: 'promoted' });
      setGroupsTick(t => t + 1);               // re-run the checks
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  const detailsTab = (
    <div className="col gap-3">
        {isDraft && projectId && (
          <div className="row gap-3" role="status" style={{ alignItems: 'center', padding: '10px 14px', borderRadius: 6,
                                                            border: '1px solid var(--border-1)', background: 'var(--bg-1)' }}>
            <span className="small" style={{ flex: 1 }}>This is a draft (a customer request). Promote it to planning to queue it.</span>
            <button className="btn sm" onClick={promote}>Promote to planning</button>
          </div>
        )}
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
              onChange={e => setFields({ name: e.target.value })}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Type</label>
            <select
              className="select"
              value={orderType}
              onChange={e => setFields({ orderType: e.target.value as OrderType })}
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
                onChange={e => setFields({ dueDate: e.target.value })}
                style={{ flex: 1 }}
              />
              {dueDate && (
                <button
                  className="btn ghost icon sm"
                  onClick={() => setFields({ dueDate: '' })}
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
                onChange={v => setFields({ customerId: v.customerId, customer: v.customerText })}
              />
            </div>
          )}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="label">Notes</label>
            <input
              className="input"
              placeholder="Optional notes"
              value={notes}
              onChange={e => setFields({ notes: e.target.value })}
            />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                          fontSize: 13, color: onHold ? 'var(--warn)' : 'var(--text-1)',
                          paddingBottom: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={onHold}
              onChange={e => setFields({ onHold: e.target.checked })}
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
              onChange={e => setFields({ price: e.target.value })}
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
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
              Links ({links.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => dispatch({ type: 'addLink' })}
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
                onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { url: e.target.value } })}
                style={{ flex: 2 }}
              />
              <input
                className="input"
                placeholder="Label (optional)"
                value={lk.label}
                onChange={e => dispatch({ type: 'updateLink', localId: lk.localId, patch: { label: e.target.value } })}
                style={{ flex: 1 }}
              />
              <button
                className="btn ghost icon sm"
                onClick={() => dispatch({ type: 'remove', kind: 'link', localId: lk.localId })}
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
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
              Non-printed parts ({parts.length})
            </span>
            <button
              className="btn ghost sm"
              onClick={() => dispatch({ type: 'addPart' })}
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
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { name: e.target.value } })}
                style={{ flex: 2 }}
              />
              <input
                type="number"
                className="input"
                min={1}
                value={pt.quantity}
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
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
                onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { unitCost: e.target.value } })}
                style={{ width: 84 }}
              />
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                              fontSize: 12, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                <input
                  type="checkbox"
                  checked={pt.allocated}
                  onChange={e => dispatch({ type: 'updatePart', localId: pt.localId, patch: { allocated: e.target.checked } })}
                />
                Allocated
              </label>
              <button
                className="btn ghost icon sm"
                onClick={() => dispatch({ type: 'remove', kind: 'part', localId: pt.localId })}
                title="Remove part"
              >
                {Icons.x}
              </button>
            </div>
          ))}
        </div>
    </div>
  );

  const itemsTab = (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 16, alignItems: 'start' }}>
      {/* Left: model file tree */}
      <div className="card" style={{ padding: 10, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 0, maxHeight: '70vh' }}>
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
        <div style={{ borderTop: '1px solid var(--border-1)', marginTop: 4, paddingTop: 6 }}>
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
                color: 'var(--text-1)', cursor: 'pointer',
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

      <div className="col gap-3" style={{ minWidth: 0 }}>
        {banner(true, false)}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>Parts ({items.length})</span>
        {bulkAsk && (
          <div className="card" style={{ padding: 12 }} data-testid="bulk-filament">
            <div className="label">Filament for {bulkAsk.ids.length} selected</div>
            <FilamentAskEditor id="bulk-ask" value={bulkAsk.value} onChange={v => setBulkAsk({ ...bulkAsk, value: v })}
                               materials={materials} catalogEnabled={catalogEnabled} />
            <div className="row gap-2" style={{ marginTop: 8, justifyContent: 'flex-end' }}>
              <button className="btn sm" onClick={() => setBulkAsk(null)}>Cancel</button>
              <button className="btn primary sm" onClick={() => {
                dispatch({ type: 'setItemsAsk', localIds: bulkAsk.ids, ask: bulkAsk.value });
                setBulkAsk(null);
              }}>Apply</button>
            </div>
          </div>
        )}
        <ItemSections items={items} groups={groups} filter={bannerSel} onBulk={onBulk}
          emptyHint={(
            <div style={{
              border: '1px dashed var(--border-1)', borderRadius: 6,
              padding: '24px 16px', textAlign: 'center',
              color: 'var(--text-4)', fontSize: 13,
            }}>
              Click an STL file on the left to add parts
            </div>
          )}
          renderRow={row => {
            const it = items.find(x => x.localId === row.localId)!;
            return (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 64px 340px auto', gap: 8, alignItems: 'start' }}>
                <span style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, paddingTop: 4 }}>
                  <span style={{ fontSize: 13, color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        title={it.file_name}>
                    {it.file_name}
                  </span>
                  <ItemSourcePicker fileId={it.file_id} fileKind={fileById.get(it.file_id)?.kind ?? (it.kind === 'stl' ? 'stl' : '3mf')}
                                    plateCount={fileById.get(it.file_id)?.plate_count ?? 1}
                                    kind={it.kind} sourceRef={it.source_ref} packRule={it.pack_rule} fixedSource={!!it.serverId}
                                    onChange={v => dispatch({ type: 'updateItem', localId: it.localId, patch: v })} />
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
                  aria-label={`Quantity of ${it.file_name}`}
                  min={1} max={99}
                  value={it.quantity}
                  onChange={e => dispatch({ type: 'updateItem', localId: it.localId, patch: { quantity: Math.max(1, parseInt(e.target.value) || 1) } })}
                  style={{ textAlign: 'center' }}
                />
                <FilamentAskEditor
                  id={`item-ask-${it.localId}`}
                  value={askOf(it)}
                  onChange={req => dispatch({ type: 'updateItem', localId: it.localId, patch: askOf(req) })}
                  materials={materials}
                  catalogEnabled={catalogEnabled}
                />
                <span style={{ display: 'flex', gap: 4 }}>
                  {items.length > 1 && (
                    <button className="btn ghost icon sm" onClick={() => dispatch({ type: 'applyFilamentToAll', ask: askOf(it) })}
                            title="Apply this filament to every part">
                      {Icons.copy}
                    </button>
                  )}
                  <button className="btn ghost icon sm" onClick={() => dispatch({ type: 'removeItems', localIds: [it.localId] })} title="Remove">
                    {Icons.x}
                  </button>
                </span>
              </div>
            );
          }} />
      </div>
    </div>
  );

  const groupsTab = !projectId ? needsSave('set up its print groups') : (
    <div className="col gap-3">
      {banner(false, true)}
      {typeof bannerSel === 'number' && (
        <PrintGroupsCard projectId={projectId} items={[]} groupId={bannerSel} refreshKey={groupsTick}
                         onChanged={() => setGroupsTick(t => t + 1)} />
      )}
      {isDirty && <div className="tiny muted">Unsaved item changes (groups of new or moved items) are saved with Save draft.</div>}
    </div>
  );

  const reviewTab = !projectId ? needsSave('check and queue it') : (
    <div className="col gap-3">
      <ReviewPanel review={review} loading={reviewLoading || saving} error={reviewError}
                   onGo={(t, ch) => { if (ch.group_id != null) setBannerSel(ch.group_id); setTab(t); }} />
      {generateResult && (
        <div style={{
          border: '1px solid var(--ok)', borderRadius: 8,
          padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        }}>
          <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
          <span style={{ fontSize: 14, flex: 1 }}>
            {generateResult.jobCount} job{generateResult.jobCount !== 1 ? 's' : ''} added to queue
            {generateResult.warnings.map(w => <span key={w} className="tiny" style={{ display: 'block', color: 'var(--warn)' }}>{w}</span>)}
          </span>
          <button className="btn sm" onClick={() => navigate('/queue')}>Queue</button>
          <button className="btn sm" onClick={() => navigate(`/projects/${projectId}`)}>Details</button>
        </div>
      )}
      {generateError && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'color-mix(in oklch, var(--err) 8%, transparent)', border: '1px solid color-mix(in oklch, var(--err) 25%, transparent)',
          borderRadius: 6, padding: '10px 14px',
        }}>
          <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
          <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{generateError}</span>
          <button className="btn sm" onClick={handleQueue} disabled={generating || saving}>Retry</button>
        </div>
      )}
    </div>
  );

  const open = review?.open ?? null;
  const queueBlocked = !projectId || isDraft || !review || reviewLoading || !!reviewError || reviewedSnap !== currentSnap
    || (open ?? 1) > 0 || items.length === 0;
  return (
    <FileDropZone onUploaded={addFile} label="Drop to upload and add to the project" style={{ height: '100%' }}>
      <div className="col gap-3" style={{ height: '100%' }}>
        <div className="row between" style={{ alignItems: 'baseline' }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>{name || 'New project'}</div>
          <div className="row gap-2">
            {isDraft && <span className="pill idle">Draft</span>}
            <button className="btn ghost sm" onClick={handleSave} disabled={!canSave || !isDirty}>
              {saving && !generating ? 'Saving…' : 'Save draft'}
            </button>
          </div>
        </div>
        <BuilderTabs tab={tab} onTab={setTab} counts={{ items: items.length, groups: groups.length, toFix: open }} />
        {saveError && <div role="alert" style={{ color: 'var(--err)', fontSize: 13 }}>Save failed: {saveError}</div>}

        <div className="card" style={{ padding: 20, overflow: 'auto', flex: 1 }}>
          {tab === 'details' && detailsTab}
          {tab === 'items' && itemsTab}
          {tab === 'groups' && groupsTab}
          {tab === 'review' && reviewTab}
        </div>

        {/* Back / Next */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="row gap-2">
            <button className="btn sm" onClick={() => navigate(projectId ? `/projects/${projectId}` : '/projects')} disabled={saving || generating}>
              Cancel
            </button>
            {at > 0 && <button className="btn ghost sm" onClick={() => setTab(ORDER[at - 1])}>← Back</button>}
          </div>
          <span className="tiny muted">{tab === 'review' && open ? `Fix ${open} issue${open === 1 ? '' : 's'} to queue` : ''}</span>
          {tab !== 'review'
            ? <button className="btn primary sm" onClick={() => setTab(ORDER[at + 1])}>Next →</button>
            : (
              <button className="btn primary sm" onClick={handleQueue} disabled={queueBlocked || !canSave}
                      title={isDraft ? 'Promote to planning before creating jobs' : undefined}>
                {generating ? 'Queueing…' : 'Queue project'}
              </button>
            )}
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
            background: 'var(--bg-1)', border: '1px solid var(--border-1)', color: 'var(--text-1)',
            padding: '10px 12px 10px 16px', borderRadius: 8,
            display: 'flex', alignItems: 'center', gap: 10,
            fontSize: 13, boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
          }}>
            <span>Removed {lastRemoved.label || lastRemoved.kind}</span>
            <button className="btn sm" onClick={() => dispatch({ type: 'undo' })}>Undo</button>
          </div>
        )}
      </div>
    </FileDropZone>
  );
}
