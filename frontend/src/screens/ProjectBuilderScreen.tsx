// Create or edit a project (`/projects/new`, `/projects/:id/edit`) in four tabs: Details, Items, Groups, Review.
// Draft state is useProjectDraft (lib/projectBuilderState); saving is api/projectSync; the sections are
// components/builder/*. This screen wires them to print groups, the Review checks and Queue project.
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { NO_ASK } from '../lib/filamentAsk';
import { askOf, parseGenerateError } from '../lib/projectDraft';
import { currentSnapshot, type HeaderFields } from '../lib/projectBuilderState';
import { useAutoDismiss } from '../components/useAutoDismiss';
import { useMediaQuery } from '../components/useMediaQuery';
import { initialSource, ItemSourcePicker } from '../components/ItemSourcePicker';
import { useProjectDraft } from '../components/builder/useProjectDraft';
import { PrintGroupsCard } from '../components/PrintGroupsCard';
import { GroupBanner, type BannerSelection } from '../components/builder/GroupBanner';
import { ItemSections, type BulkAction } from '../components/builder/ItemSections';
import { BuilderTabs, ReviewPanel, type BuilderTab } from '../components/builder/ReviewPanel';
import { ProjectMetaFields } from '../components/builder/ProjectMetaFields';
import { ProjectLinksEditor } from '../components/builder/ProjectLinksEditor';
import { ProjectPartsEditor } from '../components/builder/ProjectPartsEditor';
import { ModelPicker } from '../components/builder/ModelPicker';
import { ItemRow } from '../components/builder/ItemRow';
import { BulkFilamentCard, type BulkAsk } from '../components/builder/BulkFilamentCard';
import { QueueError, QueueSuccess } from '../components/builder/QueueResult';
import { SavedToast, UndoToast } from '../components/builder/Toasts';
import { FileDropZone } from '../components/FileDropZone';
import { promoteProject } from '../api/customers';
import { useFiles } from '../api/files';
import { useInventory, useMaterials } from '../api/inventory';
import type { LibraryFile } from '../data/types';
import {
  listProjectGroups, createProjectGroup, orderProjectGroups, getProjectReview, generateProject,
  type ProjectGroup, type ProjectReview,
  pooledGroups, generateWarnings,
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
  const { name, items, links, parts } = draft;
  const setFields = (fields: Partial<HeaderFields>) => dispatch({ type: 'setFields', fields });

  // Print groups (BIZ-360) and the banner selection
  const [groups, setGroups] = useState<ProjectGroup[]>([]);
  const [bannerSel, setBannerSel] = useState<BannerSelection>('all');
  const [groupsTick, setGroupsTick] = useState(0);
  const [bulkAsk, setBulkAsk] = useState<BulkAsk | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{
    jobCount: number; pools: number; poolParts?: number; warnings: string[]; snapshot: string; groupRevision: number;
  } | null>(null);
  // Review (BIZ-363)
  const [review, setReview] = useState<ProjectReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const [saveOk, setSaveOk] = useState(false);
  useAutoDismiss(saveOk, () => setSaveOk(false), 2000);

  const { files: allFiles } = useFiles({});
  const fileById = new Map(allFiles.map(f => [f.id, f]));
  // The model picker's folder and search live here: the Items tab unmounts it, and nothing is lost between tabs.
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const phone = useMediaQuery('(max-width: 640px)');
  const compact = useMediaQuery('(max-width: 1100px)');
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [pendingFile, setPendingFile] = useState<{
    file: LibraryFile; groupId: number | null; source: ReturnType<typeof initialSource>;
  } | null>(null);
  const [focusItem, setFocusItem] = useState<string>();

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
  const chooseFile = (file: LibraryFile) => {
    if (!compact) { addFile(file); return; }
    setPendingFile({ file, groupId: typeof bannerSel === 'number' ? bannerSel : defaultGroupId, source: initialSource(file.kind) });
  };

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
      const queuedParts = result.groups?.flatMap(group => group.pool_parts == null ? [] : [group.pool_parts]) ?? [];
      setGenerateResult({ jobCount: result.jobs.length, pools: pooledGroups(result), warnings: generateWarnings(result),
                          poolParts: queuedParts.length ? queuedParts.reduce((total, count) => total + count, 0) : undefined,
                          snapshot: currentSnap, groupRevision: groupsTick });
      loadReview(pid, currentSnap);
    } catch (e) {
      setGenerateError(parseGenerateError(e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
      setGenerating(false);
    }
  }

  const currentSnap = currentSnapshot(state);
  const receipt = generateResult?.snapshot === currentSnap && generateResult.groupRevision === groupsTick ? generateResult : null;
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
            <span className="small grow">This is a draft (a customer request). Promote it to planning to queue it.</span>
            <button className="btn sm" onClick={promote}>Promote to planning</button>
          </div>
        )}
        <ProjectMetaFields fields={draft} projectId={projectId} onChange={setFields} />
        <ProjectLinksEditor links={links} dispatch={dispatch} />
        <ProjectPartsEditor parts={parts} dispatch={dispatch} />
    </div>
  );

  const itemsTab = (
    <div className="builder-items-layout">
      <div className="col min-0 gap-2">
        {phone && (
          <button className="btn" aria-expanded={libraryOpen} aria-controls="builder-file-library"
                  onClick={() => { setLibraryOpen(!libraryOpen); setPendingFile(null); }}>
            {libraryOpen ? 'Close file library' : 'Add files'}
          </button>
        )}
        {(!phone || libraryOpen) && (
          <div id="builder-file-library">
            {pendingFile ? (
              <div className="card col gap-3 builder-file-selection">
                <strong className="builder-item-name">{pendingFile.file.original_filename}</strong>
                <ItemSourcePicker fileId={pendingFile.file.id} fileKind={pendingFile.file.kind}
                  plateCount={pendingFile.file.plate_count ?? 1} kind={pendingFile.source.kind}
                  sourceRef={pendingFile.source.source_ref} packRule={pendingFile.source.pack_rule}
                  onChange={source => setPendingFile({ ...pendingFile, source })} />
                <label className="col gap-1 small">
                  Print group
                  <select className="select" value={pendingFile.groupId ?? ''}
                          onChange={e => setPendingFile({ ...pendingFile, groupId: Number(e.target.value) || null })}>
                    {groups.length === 0 && <option value="">Default Group</option>}
                    {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                  </select>
                </label>
                <div className="row gap-2 wrap">
                  <button className="btn primary" onClick={() => {
                    dispatch({ type: 'addFile', ...pendingFile, defaultGroupId });
                    setBannerSel(pendingFile.groupId ?? 'all');
                    setPendingFile(null);
                    setLibraryOpen(false);
                  }}>Add to project</button>
                  <button className="btn" onClick={() => setPendingFile(null)}>Back to files</button>
                </div>
              </div>
            ) : (
              <ModelPicker files={allFiles} onAdd={chooseFile} selectedFolder={selectedFolder} setSelectedFolder={setSelectedFolder}
                           search={search} setSearch={setSearch} />
            )}
          </div>
        )}
      </div>

      <div className="col gap-3 min-0">
        {banner(true, false)}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>Parts ({items.length})</span>
        {bulkAsk && (
          <BulkFilamentCard bulkAsk={bulkAsk} materials={materials} catalogEnabled={catalogEnabled}
                            onChange={setBulkAsk} onCancel={() => setBulkAsk(null)}
                            onApply={() => { dispatch({ type: 'setItemsAsk', localIds: bulkAsk.ids, ask: bulkAsk.value }); setBulkAsk(null); }} />
        )}
        <ItemSections items={items} groups={groups} filter={bannerSel} onBulk={onBulk} focusItem={focusItem}
          emptyHint={(
            <div style={{
              border: '1px dashed var(--border-1)', borderRadius: 6,
              padding: '24px 16px', textAlign: 'center',
              color: 'var(--text-4)', fontSize: 13,
            }}>
              {phone ? 'Use Add files to add parts' : 'Choose a file from the library to add parts'}
            </div>
          )}
          renderRow={row => {
            const it = items.find(x => x.localId === row.localId)!;
            return (
              <ItemRow item={it} file={fileById.get(it.file_id)} serverItem={it.serverId ? serverItems.get(it.serverId) : undefined}
                       materials={materials} catalogEnabled={catalogEnabled} canApplyToAll={items.length > 1} groups={groups} dispatch={dispatch} />
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
      {receipt && (
        <QueueSuccess jobCount={receipt.jobCount} pools={receipt.pools} poolParts={receipt.poolParts}
                      warnings={receipt.warnings} onOpenProject={() => navigate(`/projects/${projectId}`)} />
      )}
      <ReviewPanel review={review} loading={reviewLoading || saving} error={reviewError}
                   onGo={(t, ch) => {
                     const item = items.find(it => it.serverId != null && it.serverId === ch.item_id);
                     if (t === 'items') {
                       setFocusItem(item?.localId);
                       setBannerSel(item?.group_id ?? ch.group_id ?? 'all');
                       setLibraryOpen(false);
                     } else if (ch.group_id != null) setBannerSel(ch.group_id);
                     setTab(t);
                   }} />
      {generateError && (
        <QueueError message={generateError} busy={generating || saving} onRetry={handleQueue} />
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

        <div className="card builder-content" key={tab}>
          {tab === 'details' && detailsTab}
          {tab === 'items' && itemsTab}
          {tab === 'groups' && groupsTab}
          {tab === 'review' && reviewTab}
        </div>

        {/* Back / Next */}
        <div className="builder-footer">
          <div className="row gap-2">
            <button className="btn sm" onClick={() => navigate(projectId ? `/projects/${projectId}` : '/projects')} disabled={saving || generating}>
              Cancel
            </button>
            {at > 0 && <button className="btn ghost sm" onClick={() => setTab(ORDER[at - 1])}>← Back</button>}
          </div>
          <span className="tiny muted">{tab === 'review' && open ? `Fix ${open} issue${open === 1 ? '' : 's'} to queue` : ''}</span>
          {tab !== 'review'
            ? <button className="btn primary sm" onClick={() => setTab(ORDER[at + 1])}>Next →</button>
            : receipt ? <button className="btn primary sm" onClick={() => navigate('/queue')}>View queue</button> : (
              <button className="btn primary sm" onClick={handleQueue} disabled={queueBlocked || !canSave}
                      title={isDraft ? 'Promote to planning before creating jobs' : undefined}>
                {generating ? 'Queueing…' : 'Queue project'}
              </button>
            )}
        </div>

        {/* Saved toast */}
        {saveOk && (
          <SavedToast />
        )}

        {/* Undo-removal toast */}
        {lastRemoved && (
          <UndoToast label={lastRemoved.label || lastRemoved.kind} onUndo={() => dispatch({ type: 'undo' })} />
        )}
      </div>
    </FileDropZone>
  );
}
