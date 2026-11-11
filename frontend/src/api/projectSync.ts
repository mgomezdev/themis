// Persisting the Project builder's draft: create a new project with all its rows, or bring an existing one in
// line with the draft (delete what was removed, update what exists, add what is new). Requests run one at a
// time, in a fixed order, so a failure leaves a predictable partial state and the caller can simply retry.
import {
  createProject, patchProject,
  addProjectItem, updateProjectItem, deleteProjectItem,
  addProjectLink, updateProjectLink, deleteProjectLink,
  addProjectPart, updateProjectPart, deleteProjectPart,
  type ProjectCreate,
} from './projects';
import { askOf, unitCostValue, type LocalPart, type ProjectDraft } from '../lib/projectDraft';

/** Server ids of saved rows removed from the draft since it was loaded or last saved. */
export interface DeletedRows {
  itemIds: number[];
  linkIds: number[];
  partIds: number[];
}

export const NO_DELETIONS: DeletedRows = { itemIds: [], linkIds: [], partIds: [] };

export type RowKind = 'item' | 'link' | 'part';

/**
 * Reported as each row request succeeds, so the caller can record it at once: if a later request fails,
 * a retry must neither re-create a row that now exists (a duplicate) nor re-delete one that is gone (a 404).
 */
export type SyncEvent =
  | { type: 'created'; kind: RowKind; localId: string; id: number }
  | { type: 'deleted'; kind: RowKind; id: number };

/** The project's own fields as the API takes them. */
export function projectFieldsOf(d: ProjectDraft): ProjectCreate {
  const isCustomer = d.orderType === 'customer';
  return {
    name: d.name,
    customer: isCustomer ? d.customer : '',
    order_type: d.orderType,
    on_hold: d.onHold,
    due_date: d.dueDate || null,
    notes: d.notes || null,
    price: d.price.trim() ? Number(d.price) : null,
    // Switching to Internal unlinks the customer account too.
    customer_id: isCustomer ? d.customerId : null,
  };
}

/** A new part's body: a blank unit cost is left out (the server default) rather than sent as null. */
const newPartBody = (pt: LocalPart) => ({
  name: pt.name, quantity: pt.quantity, allocated: pt.allocated,
  ...(pt.unitCost.trim() === '' ? {} : { unit_cost: unitCostValue(pt) }),
});

/**
 * Save `draft`, returning the project id. With `projectId` null a project is created and every row added
 * (items numbered by position). Otherwise, in order: PATCH the project; delete removed items, update saved
 * ones, add new ones; then the same for links and for parts. An edited part's blank unit cost is sent as null
 * so it is cleared. `onEvent` hears of every row created or deleted, as it happens.
 */
export async function syncProject(
  projectId: number | null, draft: ProjectDraft, deleted: DeletedRows, onEvent: (e: SyncEvent) => void = () => {},
): Promise<number> {
  const created = (kind: RowKind, localId: string, row: { id: number }) => onEvent({ type: 'created', kind, localId, id: row.id });
  const gone = (kind: RowKind, id: number) => onEvent({ type: 'deleted', kind, id });
  const fields = projectFieldsOf(draft);
  if (projectId == null) {
    const proj = await createProject(fields);
    for (const [i, it] of draft.items.entries()) {
      created('item', it.localId, await addProjectItem(proj.id, { file_id: it.file_id, quantity: it.quantity, ...askOf(it), sort_order: i }));
    }
    for (const lk of draft.links) {
      created('link', lk.localId, await addProjectLink(proj.id, { url: lk.url, label: lk.label || null }));
    }
    for (const pt of draft.parts) {
      created('part', pt.localId, await addProjectPart(proj.id, newPartBody(pt)));
    }
    return proj.id;
  }

  await patchProject(projectId, fields);
  for (const id of deleted.itemIds) {
    await deleteProjectItem(projectId, id);
    gone('item', id);
  }
  for (const it of draft.items.filter(i => i.serverId)) {
    await updateProjectItem(projectId, it.serverId!, { quantity: it.quantity, ...askOf(it), sort_order: it.sort_order });
  }
  for (const it of draft.items.filter(i => !i.serverId)) {
    created('item', it.localId, await addProjectItem(projectId, { file_id: it.file_id, quantity: it.quantity, ...askOf(it), sort_order: it.sort_order }));
  }
  for (const id of deleted.linkIds) {
    await deleteProjectLink(projectId, id);
    gone('link', id);
  }
  for (const lk of draft.links) {
    const body = { url: lk.url, label: lk.label || null };
    if (lk.serverId) await updateProjectLink(projectId, lk.serverId, body);
    else created('link', lk.localId, await addProjectLink(projectId, body));
  }
  for (const id of deleted.partIds) {
    await deleteProjectPart(projectId, id);
    gone('part', id);
  }
  for (const pt of draft.parts) {
    if (pt.serverId) {
      await updateProjectPart(projectId, pt.serverId, {
        name: pt.name, quantity: pt.quantity, allocated: pt.allocated, unit_cost: unitCostValue(pt),
      });
    } else {
      created('part', pt.localId, await addProjectPart(projectId, newPartBody(pt)));
    }
  }
  return projectId;
}
