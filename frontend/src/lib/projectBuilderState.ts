// The Project Builder's editable state, and every way it changes, as one pure reducer: the draft, the saved rows
// removed from it (deleted on the next save), the dirty-tracking baseline, and the undo snapshot. Print groups,
// the Review checks and the open tab are orchestration and stay with the screen; item edits that involve a group
// (moving rows, dropping a deleted group's id) are actions here.
import { NO_ASK, type FilamentAsk } from './filamentAsk';
import {
  askOf, draftSnapshot, newLocalId, projectToDraft,
  type LocalItem, type LocalLink, type LocalPart, type ProjectDraft, type RemovedSnapshot,
} from './projectDraft';
import { NO_DELETIONS, type DeletedRows, type SyncEvent } from '../api/projectSync';
import type { Project, ProjectItem } from '../api/projects';
import type { LibraryFile } from '../data/types';
import { RULES, initialSource, type PackRule } from '../components/ItemSourcePicker';

export interface BuilderState {
  draft: ProjectDraft;
  deleted: DeletedRows;
  /** Snapshot of the last loaded/saved draft; '' until a saved project has loaded (a new project is always dirty). */
  cleanSnap: string;
  lastRemoved: RemovedSnapshot | null;
  /** Counts removals: the undo toast's clock restarts on a new removal only, not when a save updates `lastRemoved`. */
  removalSeq: number;
  /** Loaded, not edited here: a draft-stage project (customer request) can't be queued until promoted. */
  isDraftStage: boolean;
  /** The loaded items as the server has them (print progress), by id. */
  serverItems: Map<number, ProjectItem>;
}

export type HeaderFields = Omit<ProjectDraft, 'items' | 'links' | 'parts'>;

export type BuilderAction =
  | { type: 'loaded'; project: Project }
  | { type: 'setFields'; fields: Partial<HeaderFields> }
  /** `groupId`: the group a new row joins (null = the Default Group, whose id is `defaultGroupId` once known). */
  | { type: 'addFile'; file: Pick<LibraryFile, 'id' | 'original_filename' | 'kind'>; groupId: number | null; defaultGroupId: number | null }
  | { type: 'updateItem'; localId: string; patch: Partial<LocalItem> }
  | { type: 'applyFilamentToAll'; ask: FilamentAsk }
  | { type: 'setItemsAsk'; localIds: string[]; ask: FilamentAsk }
  | { type: 'moveItems'; localIds: string[]; groupId: number | null }
  | { type: 'setItemsRule'; localIds: string[]; rule: PackRule }
  | { type: 'setItemsQuantity'; localIds: string[]; quantity: number }
  /** The project's groups were (re)listed: items of a group that no longer exists are the Default Group's now. */
  | { type: 'groupsListed'; groupIds: number[] }
  | { type: 'addLink' }
  | { type: 'updateLink'; localId: string; patch: Partial<LocalLink> }
  | { type: 'addPart' }
  | { type: 'updatePart'; localId: string; patch: Partial<LocalPart> }
  | { type: 'removeItems'; localIds: string[] }
  | { type: 'remove'; kind: 'link' | 'part'; localId: string }
  | { type: 'undo' }
  | { type: 'dismissUndo' }
  | { type: 'synced'; event: SyncEvent }
  | { type: 'saved'; snap: string }
  | { type: 'promoted' };

/** A new project's state; `?customer=<id>` pre-links an account (and makes it a customer project). */
export function initialBuilderState(newForCustomerId: number | null = null): BuilderState {
  return {
    draft: {
      name: '', customer: '', customerId: newForCustomerId, orderType: newForCustomerId ? 'customer' : 'internal',
      onHold: false, dueDate: '', notes: '', price: '', items: [], links: [], parts: [],
    },
    deleted: NO_DELETIONS,
    cleanSnap: '',
    lastRemoved: null,
    removalSeq: 0,
    isDraftStage: false,
    serverItems: new Map(),
  };
}

/** The fingerprint of `s` as it stands (pending item deletes included). */
export const currentSnapshot = (s: BuilderState) => draftSnapshot(s.draft, s.deleted.itemIds);

/** Whether Save has anything to do. A new project always does; a saved one once its draft differs from the baseline. */
export function isDirty(s: BuilderState, isExisting: boolean): boolean {
  return isExisting ? s.cleanSnap !== '' && currentSnapshot(s) !== s.cleanSnap : true;
}

const update = <R extends { localId: string }>(rows: R[], localId: string, patch: Partial<R>) =>
  rows.map(r => (r.localId === localId ? { ...r, ...patch } : r));

function insertAt<R>(rows: R[], index: number, row: R): R[] {
  const next = [...rows];
  next.splice(index, 0, row);
  return next;
}

const withoutServerId = <R extends { serverId?: number }>(r: R, id: number): R =>
  (r.serverId === id ? { ...r, serverId: undefined } : r);

export function builderReducer(s: BuilderState, a: BuilderAction): BuilderState {
  const d = s.draft;
  const items = (fn: (rows: LocalItem[]) => LocalItem[]): BuilderState => ({ ...s, draft: { ...d, items: fn(d.items) } });
  const picked = (localIds: string[], fn: (it: LocalItem) => LocalItem) =>
    items(rows => rows.map(it => (localIds.includes(it.localId) ? fn(it) : it)));

  switch (a.type) {
    case 'loaded': {
      const draft = projectToDraft(a.project);
      return {
        draft,
        deleted: NO_DELETIONS,
        cleanSnap: draftSnapshot(draft),
        // Its rows were replaced, so an undo would restore a row that no longer belongs to this draft.
        lastRemoved: null,
        removalSeq: s.removalSeq,
        isDraftStage: a.project.stage === 'draft',
        serverItems: new Map(a.project.items.map(it => [it.id, it])),
      };
    }
    case 'setFields':
      return { ...s, draft: { ...d, ...a.fields } };
    case 'addFile': {
      const target = a.groupId ?? a.defaultGroupId;
      // A repeat pick of an STL in the same group adds a copy; a 3MF / gcode pick is its own row (another plate or object).
      const existing = d.items.find(it => it.file_id === a.file.id && it.kind === 'stl' && (it.group_id ?? a.defaultGroupId) === target);
      return items(rows => (existing
        ? update(rows, existing.localId, { quantity: existing.quantity + 1 })
        : [...rows, {
            localId: newLocalId(), file_id: a.file.id, file_name: a.file.original_filename, quantity: 1,
            ...NO_ASK, sort_order: rows.length, group_id: a.groupId, ...initialSource(a.file.kind),
          }]));
    }
    case 'updateItem':
      return items(rows => update(rows, a.localId, a.patch));
    case 'applyFilamentToAll':
      return items(rows => rows.map(it => ({ ...it, ...askOf(a.ask) })));
    case 'setItemsAsk':
      return picked(a.localIds, it => ({ ...it, ...askOf(a.ask) }));
    case 'moveItems':
      return picked(a.localIds, it => ({ ...it, group_id: a.groupId }));
    case 'setItemsRule':
      // Rows whose kind can't use the rule keep their own.
      return picked(a.localIds, it => (RULES[it.kind].includes(a.rule) ? { ...it, pack_rule: a.rule } : it));
    case 'setItemsQuantity':
      return picked(a.localIds, it => ({ ...it, quantity: a.quantity }));
    case 'groupsListed': {
      const live = new Set(a.groupIds);
      const dead = (it: LocalItem) => it.group_id != null && !live.has(it.group_id);
      return d.items.some(dead) ? items(rows => rows.map(it => (dead(it) ? { ...it, group_id: null } : it))) : s;
    }
    case 'addLink':
      return { ...s, draft: { ...d, links: [...d.links, { localId: newLocalId(), url: '', label: '' }] } };
    case 'updateLink':
      return { ...s, draft: { ...d, links: update(d.links, a.localId, a.patch) } };
    case 'addPart':
      return {
        ...s,
        draft: { ...d, parts: [...d.parts, { localId: newLocalId(), name: '', quantity: 1, allocated: false, unitCost: '' }] },
      };
    case 'updatePart':
      return { ...s, draft: { ...d, parts: update(d.parts, a.localId, a.patch) } };
    case 'removeItems': {
      const removed = d.items.map((item, index) => ({ item, index })).filter(r => a.localIds.includes(r.item.localId));
      if (!removed.length) return s;
      return {
        ...s,
        draft: { ...d, items: d.items.filter(it => !a.localIds.includes(it.localId)) },
        deleted: { ...s.deleted, itemIds: [...s.deleted.itemIds, ...removed.map(r => r.item.serverId).filter((x): x is number => !!x)] },
        lastRemoved: { kind: 'item', label: removed.length === 1 ? removed[0].item.file_name : `${removed.length} items`, removed },
        removalSeq: s.removalSeq + 1,
      };
    }
    case 'remove': {
      if (a.kind === 'link') {
        const index = d.links.findIndex(l => l.localId === a.localId);
        if (index === -1) return s;
        const link = d.links[index];
        return {
          ...s,
          draft: { ...d, links: d.links.filter(l => l.localId !== a.localId) },
          deleted: link.serverId ? { ...s.deleted, linkIds: [...s.deleted.linkIds, link.serverId] } : s.deleted,
          lastRemoved: { kind: 'link', label: link.label || link.url || 'link', index, link },
          removalSeq: s.removalSeq + 1,
        };
      }
      const index = d.parts.findIndex(p => p.localId === a.localId);
      if (index === -1) return s;
      const part = d.parts[index];
      return {
        ...s,
        draft: { ...d, parts: d.parts.filter(p => p.localId !== a.localId) },
        deleted: part.serverId ? { ...s.deleted, partIds: [...s.deleted.partIds, part.serverId] } : s.deleted,
        lastRemoved: { kind: 'part', label: part.name || 'part', index, part },
        removalSeq: s.removalSeq + 1,
      };
    }
    case 'undo': {
      const r = s.lastRemoved;
      if (!r) return s;
      if (r.kind === 'item') {
        let rows = d.items;
        for (const { index, item } of [...r.removed].sort((x, y) => x.index - y.index)) rows = insertAt(rows, index, item);
        const back = new Set(r.removed.map(x => x.item.serverId).filter(Boolean));
        return {
          ...s, draft: { ...d, items: rows },
          deleted: { ...s.deleted, itemIds: s.deleted.itemIds.filter(id => !back.has(id)) }, lastRemoved: null,
        };
      }
      if (r.kind === 'link') {
        return {
          ...s, draft: { ...d, links: insertAt(d.links, r.index, r.link) },
          deleted: r.link.serverId ? { ...s.deleted, linkIds: s.deleted.linkIds.filter(id => id !== r.link.serverId) } : s.deleted,
          lastRemoved: null,
        };
      }
      return {
        ...s, draft: { ...d, parts: insertAt(d.parts, r.index, r.part) },
        deleted: r.part.serverId ? { ...s.deleted, partIds: s.deleted.partIds.filter(id => id !== r.part.serverId) } : s.deleted,
        lastRemoved: null,
      };
    }
    case 'dismissUndo':
      return s.lastRemoved ? { ...s, lastRemoved: null } : s;
    case 'synced': {
      const e = a.event;
      const key = e.kind === 'item' ? 'itemIds' : e.kind === 'link' ? 'linkIds' : 'partIds';
      const lr = s.lastRemoved;
      if (e.type === 'created') {
        const stamp = <R extends { localId: string; serverId?: number }>(r: R): R => (r.localId === e.localId ? { ...r, serverId: e.id } : r);
        const inDraft = (e.kind === 'item' ? d.items : e.kind === 'link' ? d.links : d.parts).some(r => r.localId === e.localId);
        if (inDraft) {
          if (e.kind === 'item') return { ...s, draft: { ...d, items: d.items.map(stamp) } };
          if (e.kind === 'link') return { ...s, draft: { ...d, links: d.links.map(stamp) } };
          return { ...s, draft: { ...d, parts: d.parts.map(stamp) } };
        }
        // Removed while its create was in flight: the server row now exists but the draft doesn't have it, so it is
        // deleted on the next save — and an undo restores the row with its id (updated then, not created again).
        let lastRemoved = lr;
        if (lr?.kind === 'item' && e.kind === 'item') lastRemoved = { ...lr, removed: lr.removed.map(x => ({ ...x, item: stamp(x.item) })) };
        else if (lr?.kind === 'link' && e.kind === 'link') lastRemoved = { ...lr, link: stamp(lr.link) };
        else if (lr?.kind === 'part' && e.kind === 'part') lastRemoved = { ...lr, part: stamp(lr.part) };
        return { ...s, deleted: { ...s.deleted, [key]: [...s.deleted[key], e.id] }, lastRemoved };
      }
      // The row is gone server-side now: wherever it still is (put back by an undo during the save, or in the undo
      // snapshot), it must be re-created on the next save, not updated under a deleted id.
      let lastRemoved = lr;
      if (lr?.kind === 'item' && e.kind === 'item' && lr.removed.some(x => x.item.serverId === e.id)) {
        lastRemoved = { ...lr, removed: lr.removed.map(x => ({ ...x, item: withoutServerId(x.item, e.id) })) };
      } else if (lr?.kind === 'link' && e.kind === 'link' && lr.link.serverId === e.id) {
        lastRemoved = { ...lr, link: withoutServerId(lr.link, e.id) };
      } else if (lr?.kind === 'part' && e.kind === 'part' && lr.part.serverId === e.id) {
        lastRemoved = { ...lr, part: withoutServerId(lr.part, e.id) };
      }
      const draft = e.kind === 'item' ? { ...d, items: d.items.map(r => withoutServerId(r, e.id)) }
        : e.kind === 'link' ? { ...d, links: d.links.map(r => withoutServerId(r, e.id)) }
        : { ...d, parts: d.parts.map(r => withoutServerId(r, e.id)) };
      return { ...s, draft, deleted: { ...s.deleted, [key]: s.deleted[key].filter(id => id !== e.id) }, lastRemoved };
    }
    case 'saved':
      return { ...s, cleanSnap: a.snap };
    case 'promoted':
      return { ...s, isDraftStage: false };
  }
}
