import { ItemSourcePicker, RULES, initialSource, type ItemKind, type PackRule } from '../components/ItemSourcePicker';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Icons } from '../components/icons';
import { FilamentAskEditor } from '../components/FilamentAskEditor';
import { NO_ASK, type FilamentAsk } from '../lib/filamentAsk';
import { PrintGroupsCard } from '../components/PrintGroupsCard';
import { GroupBanner, type BannerSelection } from '../components/builder/GroupBanner';
import { ItemSections, type BulkAction } from '../components/builder/ItemSections';
import { BuilderTabs, ReviewPanel, type BuilderTab } from '../components/builder/ReviewPanel';
import { CustomerPicker } from '../components/CustomerPicker';
import { FileDropZone } from '../components/FileDropZone';
import { promoteProject } from '../api/customers';
import { useFiles } from '../api/files';
import { useInventory, useMaterials } from '../api/inventory';
import type { LibraryFile, FolderNode } from '../data/types';
import {
  getProject, createProject, patchProject,
  addProjectItem, updateProjectItem, deleteProjectItem,
  listProjectGroups, createProjectGroup, orderProjectGroups, getProjectReview,
  addProjectLink, updateProjectLink, deleteProjectLink,
  addProjectPart, updateProjectPart, deleteProjectPart,
  generateProject,
  type ProjectGroup, type ProjectItem, type ProjectReview,
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

interface LocalItem extends FilamentAsk {
  localId: string;
  serverId?: number;
  file_id: number;
  file_name: string;
  quantity: number;
  sort_order: number;
  kind: ItemKind;                 // BIZ-356: STL, a 3MF plate or object, or gcode
  source_ref: string | null;
  pack_rule: PackRule;
  group_id: number | null;        // BIZ-363: its print group (null = the Default Group)
}

const askOf = (it: FilamentAsk): FilamentAsk => ({
  filament_manufacturer: it.filament_manufacturer, filament_type: it.filament_type,
  filament_subtype: it.filament_subtype, filament_color: it.filament_color,
});

interface LocalLink {
  localId: string;
  serverId?: number;
  url: string;
  label: string;
}

interface LocalPart {
  localId: string;
  serverId?: number;
  name: string;
  quantity: number;
  allocated: boolean;
  unitCost: string;   // '' = no cost entered (the field is optional)
}

/** The API value for a part's unit cost: null when blank, so an edited part can have its cost cleared. */
const unitCostValue = (pt: { unitCost: string }): number | null => (pt.unitCost.trim() === '' ? null : Number(pt.unitCost));

let _lid = 0;
const newLocalId = () => String(++_lid);

type RemovedSnapshot =
  | { kind: 'item'; label: string; removed: { index: number; item: LocalItem }[] }
  | { kind: 'link'; label: string; index: number; link: LocalLink }
  | { kind: 'part'; label: string; index: number; part: LocalPart };

const UNDO_TIMEOUT_MS = 6000;

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

  // Project header fields
  const [name, setName] = useState('');
  const [customer, setCustomer] = useState('');
  const [customerId, setCustomerId] = useState<number | null>(newForCustomerId);
  const [orderType, setOrderType] = useState<'internal' | 'customer'>(newForCustomerId ? 'customer' : 'internal');
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
  // Print groups (BIZ-360) and the banner selection
  const [groups, setGroups] = useState<ProjectGroup[]>([]);
  const [bannerSel, setBannerSel] = useState<BannerSelection>('all');
  const [groupsTick, setGroupsTick] = useState(0);
  const [bulkAsk, setBulkAsk] = useState<{ ids: string[]; value: FilamentAsk } | null>(null);
  // Links
  const [links, setLinks] = useState<LocalLink[]>([]);
  const [deletedLinkIds, setDeletedLinkIds] = useState<number[]>([]);
  // Non-printed parts (hardware, e.g. magnets/screws)
  const [parts, setParts] = useState<LocalPart[]>([]);
  const [deletedPartIds, setDeletedPartIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [generateResult, setGenerateResult] = useState<{ jobCount: number; warnings: string[] } | null>(null);
  // Review (BIZ-363)
  const [review, setReview] = useState<ProjectReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

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
        for (const { index, item } of [...lastRemoved.removed].sort((a, b) => a.index - b.index)) next.splice(index, 0, item);
        return next;
      });
      const back = new Set(lastRemoved.removed.map(r => r.item.serverId).filter(Boolean));
      setDeletedItemIds(prev => prev.filter(id => !back.has(id)));
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

  function computeSnap(
    n: string, c: string, ot: string, oh: boolean, dd: string, no: string,
    its: LocalItem[], lks: LocalLink[], prts: LocalPart[],
    pr: string = price,
    cid: number | null = customerId,
    del: number[] = deletedItemIds,
  ) {
    return JSON.stringify({
      name: n, customer: ot === 'customer' ? c : '', customerId: ot === 'customer' ? cid : null, orderType: ot,
      onHold: oh, dueDate: dd, notes: no, price: pr,
      items: its.map(i => ({
        fid: i.file_id, qty: i.quantity, rule: i.pack_rule, gid: i.group_id,
        fm: i.filament_manufacturer, ft: i.filament_type, fs: i.filament_subtype, fc: i.filament_color, so: i.sort_order,
      })),
      del,
      // Server ids are left out: a save fills them in, and that is not an edit.
      links: lks.map(l => ({ url: l.url, label: l.label })),
      parts: prts.map(p => ({ name: p.name, qty: p.quantity, alloc: p.allocated, uc: p.unitCost })),
    });
  }

  // File tree state
  const { files: allFiles } = useFiles({});
  // Every model and pre-sliced file can be an item (BIZ-356): STLs, 3MF plates/objects, gcode plates.
  const stlFiles = allFiles.filter(f => /\.(stl|3mf|gcode)$/i.test(f.original_filename) && !f.missing);
  const fileById = new Map(allFiles.map(f => [f.id, f]));
  const [selectedFolder, setSelectedFolder] = useState('');
  const [search, setSearch] = useState('');
  const searchLower = search.trim().toLowerCase();
  const folderTree = buildStlTree(stlFiles);
  const folderFiles = searchLower
    ? stlFiles.filter(f => f.original_filename.toLowerCase().includes(searchLower))
    : stlFiles.filter(f => {
        const norm = f.folder.replace(/^\//, '');
        return selectedFolder === '' ? true : norm === selectedFolder;
      });

  // Load existing project
  useEffect(() => {
    if (!projectId) return;
    getProject(projectId).then(p => {
      const n = p.name;
      const c = p.customer ?? '';
      // A project linked to an account is a customer project, whatever its old type said —
      // otherwise an unrelated save would unlink it.
      const ot = p.customer_id != null ? 'customer' : (p.order_type as 'internal' | 'customer') ?? 'internal';
      const oh = p.on_hold ?? false;
      const dd = p.due_date ?? '';
      const no = p.notes ?? '';
      const pr = p.price != null ? String(p.price) : '';
      const cid = p.customer_id;
      // A linked account's current name wins over whatever was typed before it was linked.
      const cName = cid != null && p.customer_name ? p.customer_name : c;
      const its: LocalItem[] = p.items.map(it => ({
        localId: newLocalId(),
        serverId: it.id,
        file_id: it.file_id,
        file_name: it.file_name,
        quantity: it.quantity,
        kind: it.kind ?? 'stl', source_ref: it.source_ref ?? null, pack_rule: it.pack_rule ?? 'split',
        group_id: it.group_id ?? null,
        filament_manufacturer: it.filament_manufacturer,
        filament_type: it.filament_type,
        filament_subtype: it.filament_subtype,
        filament_color: it.filament_color,
        sort_order: it.sort_order,
      }));
      const lks: LocalLink[] = (p.links ?? []).map(l => ({
        localId: newLocalId(),
        serverId: l.id,
        url: l.url,
        label: l.label ?? '',
      }));
      const prts: LocalPart[] = (p.parts ?? []).map(pt => ({
        localId: newLocalId(),
        serverId: pt.id,
        name: pt.name,
        quantity: pt.quantity,
        allocated: pt.allocated,
        unitCost: pt.unit_cost != null ? String(pt.unit_cost) : '',
      }));
      setName(n); setCustomer(cName); setCustomerId(cid); setOrderType(ot); setOnHold(oh);
      setDueDate(dd); setNotes(no); setPrice(pr); setIsDraft(p.stage === 'draft');
      setItems(its); setLinks(lks); setParts(prts);
      setDeletedLinkIds([]);
      setDeletedPartIds([]);
      setDeletedItemIds([]);
      setServerItems(new Map(p.items.map(it => [it.id, it])));
      setCleanSnap(computeSnap(n, cName, ot, oh, dd, no, its, lks, prts, pr, cid, []));
    }).catch(console.error);
  }, [projectId]);

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
      setItems(prev => (prev.some(it => it.group_id != null && !ids.has(it.group_id))
        ? prev.map(it => (it.group_id != null && !ids.has(it.group_id) ? { ...it, group_id: null } : it)) : prev));
      setBannerSel(sel => (sel === 'all' || ids.has(sel) ? sel : 'all'));
    }).catch(() => {});
    return () => { alive = false; };
  }, [projectId, groupsTick]);
  const defaultGroupId = groups.find(g => g.is_default)?.id ?? null;
  // The Groups tab has no "All": it always edits one group.
  useEffect(() => {
    if (tab === 'groups' && (bannerSel === 'all' || !groups.some(g => g.id === bannerSel)) && groups.length) {
      setBannerSel(defaultGroupId ?? groups[0].id);
    }
  }, [tab, bannerSel, groups, defaultGroupId]);

  function addFile(f: LibraryFile) {
    // New rows join the badge being shown (BIZ-363), else the Default Group.
    const gid = typeof bannerSel === 'number' ? bannerSel : null;
    setItems(prev => {
      // A repeat pick of an STL adds a copy; a 3MF / gcode pick is its own row (another plate or object of it).
      const existing = prev.find(it => it.file_id === f.id && it.kind === 'stl' && (it.group_id ?? defaultGroupId) === (gid ?? defaultGroupId));
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
          group_id: gid,
          ...initialSource(f.kind),
        },
      ];
    });
  }

  function updateItem(localId: string, patch: Partial<LocalItem>) {
    setItems(prev => prev.map(it => it.localId === localId ? { ...it, ...patch } : it));
  }

  function removeItems(localIds: string[]) {
    const removed = items.map((item, index) => ({ item, index })).filter(r => localIds.includes(r.item.localId));
    if (!removed.length) return;
    setLastRemoved({ kind: 'item', label: removed.length === 1 ? removed[0].item.file_name : `${removed.length} items`, removed });
    setItems(prev => prev.filter(it => !localIds.includes(it.localId)));
    setDeletedItemIds(prev => [...prev, ...removed.map(r => r.item.serverId).filter((x): x is number => !!x)]);
    scheduleUndoClear();
  }
  const removeItem = (localId: string) => removeItems([localId]);

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

  async function newGroup(moveIds: string[] = []) {
    if (!projectId) return;
    const taken = new Set(groups.map(g => g.name));
    let n = groups.length;
    while (taken.has(`Group ${n}`)) n++;
    try {
      const g = await createProjectGroup(projectId, `Group ${n}`);
      if (moveIds.length) setItems(prev => prev.map(it => (moveIds.includes(it.localId) ? { ...it, group_id: g.id } : it)));
      setBannerSel(g.id);
      setGroupsTick(t => t + 1);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  function onBulk(ids: string[], action: BulkAction) {
    const pick = (fn: (it: LocalItem) => LocalItem) => setItems(prev => prev.map(it => (ids.includes(it.localId) ? fn(it) : it)));
    if (action.kind === 'group') pick(it => ({ ...it, group_id: action.groupId }));
    else if (action.kind === 'rule') pick(it => (RULES[it.kind].includes(action.rule) ? { ...it, pack_rule: action.rule } : it));
    else if (action.kind === 'qty') pick(it => ({ ...it, quantity: action.quantity }));
    else if (action.kind === 'remove') removeItems(ids);
    else {
      const first = items.find(it => ids.includes(it.localId));
      setBulkAsk({ ids, value: first ? askOf(first) : { ...NO_ASK } });
    }
  }

  async function saveProject(): Promise<number> {
    const projectFields = {
      name,
      customer: orderType === 'customer' ? customer : '',
      order_type: orderType,
      on_hold: onHold,
      due_date: dueDate || null,
      notes: notes || null,
      price: price.trim() ? Number(price) : null,
      // Switching to Internal unlinks the customer account too.
      customer_id: orderType === 'customer' ? customerId : null,
    };
    if (projectId) {
      await patchProject(projectId, projectFields);
      for (const id of deletedItemIds) {
        await deleteProjectItem(projectId, id);
        setDeletedItemIds(prev => prev.filter(x => x !== id));
      }
      const existing = items.filter(i => i.serverId);
      const newOnes = items.filter(i => !i.serverId);
      for (const it of existing) {
        await updateProjectItem(projectId, it.serverId!, {
          quantity: it.quantity,
          ...askOf(it),
          sort_order: it.sort_order,
          pack_rule: it.pack_rule,
          ...(it.group_id != null ? { group_id: it.group_id } : {}),
        });
      }
      for (let i = 0; i < newOnes.length; i++) {
        const it = newOnes[i];
        const added = await addProjectItem(projectId, {
          file_id: it.file_id,
          quantity: it.quantity,
          ...askOf(it),
          sort_order: it.sort_order,
          kind: it.kind, source_ref: it.source_ref, pack_rule: it.pack_rule,
          ...(it.group_id != null ? { group_id: it.group_id } : {}),
        });
        // Later saves update this row instead of adding it again.
        if (added && typeof added.id === 'number') updateItem(it.localId, { serverId: added.id });
      }
      for (const id of deletedLinkIds) {
        await deleteProjectLink(projectId, id);
        setDeletedLinkIds(prev => prev.filter(x => x !== id));
      }
      for (const lk of links) {
        if (lk.serverId) {
          await updateProjectLink(projectId, lk.serverId, { url: lk.url, label: lk.label || null });
        } else {
          // 1. Later saves (the Builder stays open) update this link instead of adding it again.
          const added = await addProjectLink(projectId, { url: lk.url, label: lk.label || null });
          if (added && typeof added.id === 'number') {
            setLinks(prev => prev.map(l => (l.localId === lk.localId ? { ...l, serverId: added.id } : l)));
          }
        }
      }
      for (const id of deletedPartIds) {
        await deleteProjectPart(projectId, id);
        setDeletedPartIds(prev => prev.filter(x => x !== id));
      }
      for (const pt of parts) {
        if (pt.serverId) {
          await updateProjectPart(projectId, pt.serverId, {
            name: pt.name, quantity: pt.quantity, allocated: pt.allocated, unit_cost: unitCostValue(pt),
          });
        } else {
          const added = await addProjectPart(projectId, {
            name: pt.name, quantity: pt.quantity, allocated: pt.allocated,
            ...(pt.unitCost.trim() === '' ? {} : { unit_cost: unitCostValue(pt) }),
          });
          if (added && typeof added.id === 'number') {
            setParts(prev => prev.map(p => (p.localId === pt.localId ? { ...p, serverId: added.id } : p)));
          }
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
          ...askOf(it),
          sort_order: i,
          kind: it.kind, source_ref: it.source_ref, pack_rule: it.pack_rule,
        });
      }
      for (const lk of links) {
        await addProjectLink(proj.id, { url: lk.url, label: lk.label || null });
      }
      for (const pt of parts) {
        await addProjectPart(proj.id, {
          name: pt.name, quantity: pt.quantity, allocated: pt.allocated,
          ...(pt.unitCost.trim() === '' ? {} : { unit_cost: unitCostValue(pt) }),
        });
      }
      return proj.id;
    }
  }

  /** Save draft: stays in the Builder (a new project moves to its /edit route, so later saves update it). */
  async function handleSave(): Promise<number | null> {
    if (!name.trim()) return null;
    setSaving(true);
    setSaveError('');
    try {
      const pid = await saveProject();
      setDeletedLinkIds([]);
      setDeletedPartIds([]);
      setDeletedItemIds([]);
      setCleanSnap(computeSnap(name, customer, orderType, onHold, dueDate, notes, items, links, parts, price, customerId, []));
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
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
      const pid = await saveProject();
      setDeletedItemIds([]);
      setCleanSnap(computeSnap(name, customer, orderType, onHold, dueDate, notes, items, links, parts, price, customerId, []));
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

  const currentSnap = computeSnap(name, customer, orderType, onHold, dueDate, notes, items, links, parts);
  const isDirty = projectId ? (cleanSnap !== '' && currentSnap !== cleanSnap) : true;
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
                   else setItems(prev => prev.map(it => (ids.includes(it.localId) ? { ...it, group_id: target } : it)));
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
      setIsDraft(false);
      setGroupsTick(t => t + 1);               // re-run the checks
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  const detailsTab = (
    <div className="col gap-3">
        {isDraft && projectId && (
          <div className="row gap-3" role="status" style={{ alignItems: 'center', padding: '10px 14px', borderRadius: 6,
                                                            border: '1px solid var(--border)', background: 'var(--bg-2)' }}>
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
          <FolderRow node={folderTree} depth={0} selected={selectedFolder} onSelect={setSelectedFolder} />
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

      <div className="col gap-3" style={{ minWidth: 0 }}>
        {banner(true, false)}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>Parts ({items.length})</span>
        {bulkAsk && (
          <div className="card" style={{ padding: 12 }} data-testid="bulk-filament">
            <div className="label">Filament for {bulkAsk.ids.length} selected</div>
            <FilamentAskEditor id="bulk-ask" value={bulkAsk.value} onChange={v => setBulkAsk({ ...bulkAsk, value: v })}
                               materials={materials} catalogEnabled={catalogEnabled} />
            <div className="row gap-2" style={{ marginTop: 8, justifyContent: 'flex-end' }}>
              <button className="btn sm" onClick={() => setBulkAsk(null)}>Cancel</button>
              <button className="btn primary sm" onClick={() => {
                setItems(prev => prev.map(it => (bulkAsk.ids.includes(it.localId) ? { ...it, ...askOf(bulkAsk.value) } : it)));
                setBulkAsk(null);
              }}>Apply</button>
            </div>
          </div>
        )}
        <ItemSections items={items} groups={groups} filter={bannerSel} onBulk={onBulk}
          emptyHint={(
            <div style={{
              border: '1px dashed var(--border)', borderRadius: 6,
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
                                    onChange={v => updateItem(it.localId, v)} />
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
                  onChange={e => updateItem(it.localId, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                  style={{ textAlign: 'center' }}
                />
                <FilamentAskEditor
                  id={`item-ask-${it.localId}`}
                  value={askOf(it)}
                  onChange={req => setItemFilament(it.localId, req)}
                  materials={materials}
                  catalogEnabled={catalogEnabled}
                />
                <span style={{ display: 'flex', gap: 4 }}>
                  {items.length > 1 && (
                    <button className="btn ghost icon sm" onClick={() => applyFilamentToAll(askOf(it))}
                            title="Apply this filament to every part">
                      {Icons.copy}
                    </button>
                  )}
                  <button className="btn ghost icon sm" onClick={() => removeItem(it.localId)} title="Remove">
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
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
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
    <FileDropZone onUploaded={addFile} label="Drop to upload and add to the project">
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
    </FileDropZone>
  );
}
