// Create or edit a project (`/projects/new`, `/projects/:id/edit`) in four tabs: Details, Items, Groups, Review.
// Draft state is useProjectDraft (lib/projectBuilderState); saving is api/projectSync; the sections are
// components/builder/*. This screen wires them to print groups, the Review checks and Queue project.
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { NO_ASK } from '../lib/filamentAsk';
import { askOf, parseGenerateError } from '../lib/projectDraft';
import { currentSnapshot, type HeaderFields } from '../lib/projectBuilderState';
import { useAutoDismiss } from '../components/useAutoDismiss';
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
  const [generateResult, setGenerateResult] = useState<{ jobCount: number; warnings: string[] } | null>(null);
  // Review (BIZ-363)
  const [review, setReview] = useState<ProjectReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const [saveOk, setSaveOk] = useState(false);
  useAutoDismiss(saveOk, () => setSaveOk(false), 2000);

  const { files: allFiles } = useFiles({});
  const fileById = new Map(allFiles.map(f => [f.id, f]));

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
        <ProjectMetaFields fields={draft} projectId={projectId} onChange={setFields} />
        <ProjectLinksEditor links={links} dispatch={dispatch} />
        <ProjectPartsEditor parts={parts} dispatch={dispatch} />
    </div>
  );

  const itemsTab = (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 16, alignItems: 'start' }}>
      {/* Left: model file tree */}
      <ModelPicker files={allFiles} onAdd={addFile} />

      <div className="col gap-3" style={{ minWidth: 0 }}>
        {banner(true, false)}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>Parts ({items.length})</span>
        {bulkAsk && (
          <BulkFilamentCard bulkAsk={bulkAsk} materials={materials} catalogEnabled={catalogEnabled}
                            onChange={setBulkAsk} onCancel={() => setBulkAsk(null)}
                            onApply={() => { dispatch({ type: 'setItemsAsk', localIds: bulkAsk.ids, ask: bulkAsk.value }); setBulkAsk(null); }} />
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
              <ItemRow item={it} file={fileById.get(it.file_id)} serverItem={it.serverId ? serverItems.get(it.serverId) : undefined}
                       materials={materials} catalogEnabled={catalogEnabled} canApplyToAll={items.length > 1} dispatch={dispatch} />
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
        <QueueSuccess jobCount={generateResult.jobCount} warnings={generateResult.warnings}
                      onOpenQueue={() => navigate('/queue')} onOpenProject={() => navigate(`/projects/${projectId}`)} />
      )}
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
