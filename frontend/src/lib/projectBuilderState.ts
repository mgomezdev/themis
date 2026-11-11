// All of the Project builder's editable state, and every way it changes, as one pure reducer: the draft, the
// saved rows removed from it (deleted on the next save), the dirty-tracking baseline, and the undo snapshot.
import { NO_ASK, type FilamentAsk } from './filamentAsk';
import {
  askOf, draftSnapshot, newLocalId, projectToDraft,
  type LocalItem, type LocalLink, type LocalPart, type ProjectDraft, type RemovedSnapshot,
} from './projectDraft';
import { NO_DELETIONS, type DeletedRows, type RowKind, type SyncEvent } from '../api/projectSync';
import type { Project, ProjectItem } from '../api/projects';
import type { LibraryFile } from '../data/types';

export interface BuilderState {
  draft: ProjectDraft;
  deleted: DeletedRows;
  /** Snapshot of the last loaded/saved draft; '' until a saved project has loaded (a new project is always dirty). */
  cleanSnap: string;
  lastRemoved: RemovedSnapshot | null;
  /** Loaded, not edited here: a draft-stage project (customer request) can't generate jobs until promoted. */
  isDraftStage: boolean;
  /** The loaded items as the server has them (print progress), by id. */
  serverItems: Map<number, ProjectItem>;
}

export type HeaderFields = Omit<ProjectDraft, 'items' | 'links' | 'parts'>;

export type BuilderAction =
  | { type: 'loaded'; project: Project }
  | { type: 'setFields'; fields: Partial<HeaderFields> }
  | { type: 'addFile'; file: Pick<LibraryFile, 'id' | 'original_filename'> }
  | { type: 'updateItem'; localId: string; patch: Partial<LocalItem> }
  | { type: 'applyFilamentToAll'; ask: FilamentAsk }
  | { type: 'addLink' }
  | { type: 'updateLink'; localId: string; patch: Partial<LocalLink> }
  | { type: 'addPart' }
  | { type: 'updatePart'; localId: string; patch: Partial<LocalPart> }
  | { type: 'remove'; kind: RowKind; localId: string }
  | { type: 'undo' }
  | { type: 'dismissUndo' }
  | { type: 'synced'; event: SyncEvent }
  | { type: 'saved'; snap: string };

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
    isDraftStage: false,
    serverItems: new Map(),
  };
}

/** Whether Save has anything to do. A new project always does; a saved one once its draft differs from the baseline. */
export function isDirty(s: BuilderState, isExisting: boolean): boolean {
  return isExisting ? s.cleanSnap !== '' && draftSnapshot(s.draft) !== s.cleanSnap : true;
}

/** `rows` with each row whose `localId` is in `ids` given that server id. */
export function withServerIds<R extends { localId: string; serverId?: number }>(rows: R[], ids: ReadonlyMap<string, number>): R[] {
  return rows.map(r => (ids.has(r.localId) ? { ...r, serverId: ids.get(r.localId) } : r));
}

const LIST = { item: 'items', link: 'links', part: 'parts' } as const;
const DELETED = { item: 'itemIds', link: 'linkIds', part: 'partIds' } as const;

const update = <R extends { localId: string }>(rows: R[], localId: string, patch: Partial<R>) =>
  rows.map(r => (r.localId === localId ? { ...r, ...patch } : r));

function insertAt<R>(rows: R[], index: number, row: R): R[] {
  const next = [...rows];
  next.splice(index, 0, row);
  return next;
}

function removedRow(r: RemovedSnapshot): LocalItem | LocalLink | LocalPart {
  return r.kind === 'item' ? r.item : r.kind === 'link' ? r.link : r.part;
}

export function builderReducer(s: BuilderState, a: BuilderAction): BuilderState {
  const d = s.draft;
  switch (a.type) {
    case 'loaded': {
      const draft = projectToDraft(a.project);
      return {
        draft,
        deleted: NO_DELETIONS,
        cleanSnap: draftSnapshot(draft),
        // Its rows were replaced, so an undo would restore a row that no longer belongs to this draft.
        lastRemoved: null,
        isDraftStage: a.project.stage === 'draft',
        serverItems: new Map(a.project.items.map(it => [it.id, it])),
      };
    }
    case 'setFields':
      return { ...s, draft: { ...d, ...a.fields } };
    case 'addFile': {
      // Clicking a file that's already a part bumps its quantity instead of adding a duplicate row.
      const existing = d.items.find(it => it.file_id === a.file.id);
      const items = existing
        ? update(d.items, existing.localId, { quantity: existing.quantity + 1 })
        : [...d.items, {
            localId: newLocalId(), file_id: a.file.id, file_name: a.file.original_filename, quantity: 1,
            ...NO_ASK, sort_order: d.items.length,
          }];
      return { ...s, draft: { ...d, items } };
    }
    case 'updateItem':
      return { ...s, draft: { ...d, items: update(d.items, a.localId, a.patch) } };
    case 'applyFilamentToAll':
      return { ...s, draft: { ...d, items: d.items.map(it => ({ ...it, ...askOf(a.ask) })) } };
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
    case 'remove': {
      const rows: (LocalItem | LocalLink | LocalPart)[] = d[LIST[a.kind]];
      const index = rows.findIndex(r => r.localId === a.localId);
      if (index === -1) return s;
      const row = rows[index];
      let lastRemoved: RemovedSnapshot;
      if (a.kind === 'item') { const item = row as LocalItem; lastRemoved = { kind: 'item', label: item.file_name, index, item }; }
      else if (a.kind === 'link') { const link = row as LocalLink; lastRemoved = { kind: 'link', label: link.label || link.url || 'link', index, link }; }
      else { const part = row as LocalPart; lastRemoved = { kind: 'part', label: part.name || 'part', index, part }; }
      const key = DELETED[a.kind];
      return {
        ...s,
        draft: { ...d, [LIST[a.kind]]: rows.filter(r => r.localId !== a.localId) },
        deleted: row.serverId ? { ...s.deleted, [key]: [...s.deleted[key], row.serverId] } : s.deleted,
        lastRemoved,
      };
    }
    case 'undo': {
      const r = s.lastRemoved;
      if (!r) return s;
      const row = removedRow(r);
      const key = DELETED[r.kind];
      return {
        ...s,
        draft: { ...d, [LIST[r.kind]]: insertAt<LocalItem | LocalLink | LocalPart>(d[LIST[r.kind]], r.index, row) },
        deleted: row.serverId ? { ...s.deleted, [key]: s.deleted[key].filter(id => id !== row.serverId) } : s.deleted,
        lastRemoved: null,
      };
    }
    case 'dismissUndo':
      return s.lastRemoved ? { ...s, lastRemoved: null } : s;
    case 'synced': {
      const e = a.event;
      if (e.type === 'created') {
        const ids = new Map([[e.localId, e.id]]);
        return { ...s, draft: { ...d, [LIST[e.kind]]: withServerIds<LocalItem | LocalLink | LocalPart>(d[LIST[e.kind]], ids) } };
      }
      const key = DELETED[e.kind];
      let lastRemoved = s.lastRemoved;
      // The removed row is gone server-side now: undoing must re-create it, not update a deleted id.
      if (lastRemoved && lastRemoved.kind === e.kind && removedRow(lastRemoved).serverId === e.id) {
        lastRemoved = lastRemoved.kind === 'item' ? { ...lastRemoved, item: { ...lastRemoved.item, serverId: undefined } }
          : lastRemoved.kind === 'link' ? { ...lastRemoved, link: { ...lastRemoved.link, serverId: undefined } }
          : { ...lastRemoved, part: { ...lastRemoved.part, serverId: undefined } };
      }
      return { ...s, deleted: { ...s.deleted, [key]: s.deleted[key].filter(id => id !== e.id) }, lastRemoved };
    }
    case 'saved':
      return { ...s, cleanSnap: a.snap };
  }
}
