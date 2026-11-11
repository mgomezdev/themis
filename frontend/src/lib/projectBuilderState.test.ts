import { describe, expect, it } from 'vitest';
import {
  builderReducer, initialBuilderState, isDirty, withServerIds,
  type BuilderAction, type BuilderState,
} from './projectBuilderState';
import { draftSnapshot, type LocalItem, type LocalLink, type LocalPart } from './projectDraft';
import type { Project } from '../api/projects';

const item = (localId: string, file_id: number, extra: Partial<LocalItem> = {}): LocalItem => ({
  localId, file_id, file_name: `${file_id}.stl`, quantity: 1, sort_order: 0,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, ...extra,
});
const link = (localId: string, extra: Partial<LocalLink> = {}): LocalLink => ({ localId, url: '', label: '', ...extra });
const part = (localId: string, extra: Partial<LocalPart> = {}): LocalPart => ({
  localId, name: '', quantity: 1, allocated: false, unitCost: '', ...extra,
});

/** A state with saved rows: items i1 (50), i2 (51), link l1 (70), part p1 (80). */
function savedState(): BuilderState {
  const s = initialBuilderState();
  return {
    ...s,
    draft: {
      ...s.draft, name: 'Shelf',
      items: [item('i1', 1, { serverId: 50 }), item('i2', 2, { serverId: 51, sort_order: 1 })],
      links: [link('l1', { serverId: 70, url: 'https://x.test', label: 'Spec' })],
      parts: [part('p1', { serverId: 80, name: 'magnet' })],
    },
  };
}
const run = (s: BuilderState, ...actions: BuilderAction[]) => actions.reduce(builderReducer, s);
const ids = (rows: { localId: string }[]) => rows.map(r => r.localId);

const PROJECT = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  price: null, customer_id: null, customer_name: null, stage: 'draft',
  items: [{ id: 50, project_id: 7, file_id: 1, file_name: '1.stl', quantity: 2, quantity_completed: 1, quantity_failed: 0,
            filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0 }],
  links: [], parts: [],
} as unknown as Project;

describe('initialBuilderState', () => {
  it('is an empty internal project', () => {
    const s = initialBuilderState();
    expect(s.draft).toMatchObject({ name: '', orderType: 'internal', customerId: null, items: [], links: [], parts: [] });
    expect(s).toMatchObject({ cleanSnap: '', lastRemoved: null, isDraftStage: false, deleted: { itemIds: [], linkIds: [], partIds: [] } });
  });

  it('pre-links a customer account as a customer project', () => {
    expect(initialBuilderState(3).draft).toMatchObject({ customerId: 3, orderType: 'customer' });
  });
});

describe('isDirty', () => {
  it('is always true for a new project', () => {
    expect(isDirty(initialBuilderState(), false)).toBe(true);
  });

  it('is false for a saved project until it has loaded', () => {
    expect(isDirty(initialBuilderState(), true)).toBe(false);
  });

  it('compares a saved project\'s draft with its baseline', () => {
    const s = run(initialBuilderState(), { type: 'loaded', project: PROJECT });
    expect(isDirty(s, true)).toBe(false);
    expect(isDirty(run(s, { type: 'setFields', fields: { notes: 'x' } }), true)).toBe(true);
    expect(isDirty(run(s, { type: 'setFields', fields: { notes: 'x' } }, { type: 'setFields', fields: { notes: '' } }), true)).toBe(false);
  });
});

describe('withServerIds', () => {
  it('stamps only the listed rows, leaving the rest (and the input) unchanged', () => {
    const rows = [link('a'), link('b', { serverId: 9 }), link('c')];
    const out = withServerIds(rows, new Map([['a', 1], ['b', 2]]));
    expect(out.map(r => r.serverId)).toEqual([1, 2, undefined]);
    expect(rows.map(r => r.serverId)).toEqual([undefined, 9, undefined]);
  });
});

describe('loaded', () => {
  it('replaces the draft, baseline, deletions and undo, and keeps the server view', () => {
    const before = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' });
    const s = run(before, { type: 'loaded', project: PROJECT });
    expect(s.draft.name).toBe('Gridfinity');
    expect(s.cleanSnap).toBe(draftSnapshot(s.draft));
    expect(s.deleted).toEqual({ itemIds: [], linkIds: [], partIds: [] });
    expect(s.lastRemoved).toBeNull();
    expect(s.isDraftStage).toBe(true);
    expect(s.serverItems.get(50)?.quantity_completed).toBe(1);
  });

  it('is not a draft stage for any other stage', () => {
    expect(run(initialBuilderState(), { type: 'loaded', project: { ...PROJECT, stage: 'planning' } as Project }).isDraftStage).toBe(false);
  });
});

describe('editing', () => {
  it('sets header fields without touching the rest', () => {
    const s0 = savedState();
    const s = run(s0, { type: 'setFields', fields: { notes: 'rush', onHold: true } });
    expect(s.draft).toMatchObject({ name: 'Shelf', notes: 'rush', onHold: true });
    expect(s.draft.items).toBe(s0.draft.items);
    expect(s.deleted).toBe(s0.deleted);
  });

  it('adds a file as a new last item with no filament ask', () => {
    const s = run(savedState(), { type: 'addFile', file: { id: 3, original_filename: 'C.stl' } });
    expect(s.draft.items).toHaveLength(3);
    expect(s.draft.items[2]).toEqual({
      localId: expect.any(String), file_id: 3, file_name: 'C.stl', quantity: 1, sort_order: 2,
      filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null,
    });
    expect(s.draft.items[2].serverId).toBeUndefined();
  });

  it('bumps the quantity of a file that is already a part instead of adding it again', () => {
    const s = run(savedState(), { type: 'addFile', file: { id: 2, original_filename: '2.stl' } }, { type: 'addFile', file: { id: 2, original_filename: '2.stl' } });
    expect(s.draft.items.map(i => i.quantity)).toEqual([1, 3]);
  });

  it('updates one item, link or part by local id', () => {
    const s = run(savedState(),
      { type: 'updateItem', localId: 'i2', patch: { quantity: 4 } },
      { type: 'updateLink', localId: 'l1', patch: { label: 'New' } },
      { type: 'updatePart', localId: 'p1', patch: { unitCost: '2' } });
    expect(s.draft.items.map(i => i.quantity)).toEqual([1, 4]);
    expect(s.draft.links[0]).toMatchObject({ label: 'New', url: 'https://x.test', serverId: 70 });
    expect(s.draft.parts[0]).toMatchObject({ unitCost: '2', name: 'magnet', serverId: 80 });
  });

  it('applies one filament ask to every item (and nothing else)', () => {
    const ask = { filament_manufacturer: 'Acme', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000' };
    const s = run(savedState(), { type: 'applyFilamentToAll', ask: { ...ask, extra: 1 } as typeof ask });
    for (const it of s.draft.items) expect(it).toMatchObject(ask);
    expect(s.draft.items[0]).not.toHaveProperty('extra');
    expect(s.draft.items.map(i => i.file_id)).toEqual([1, 2]);
  });

  it('adds a blank link and a blank part (quantity 1) with fresh local ids', () => {
    const s = run(savedState(), { type: 'addLink' }, { type: 'addPart' });
    expect(s.draft.links[1]).toEqual({ localId: expect.any(String), url: '', label: '' });
    expect(s.draft.parts[1]).toEqual({ localId: expect.any(String), name: '', quantity: 1, allocated: false, unitCost: '' });
    expect(s.draft.links[1].localId).not.toBe(s.draft.parts[1].localId);
  });
});

describe('remove / undo', () => {
  it.each([
    ['item', 'i1', 'itemIds', 50, '1.stl'],
    ['link', 'l1', 'linkIds', 70, 'Spec'],
    ['part', 'p1', 'partIds', 80, 'magnet'],
  ] as const)('removing a saved %s queues its deletion; undo restores it in place and un-queues it', (kind, localId, key, id, label) => {
    const s0 = savedState();
    const removed = run(s0, { type: 'remove', kind, localId });
    expect(removed.deleted[key]).toEqual([id]);
    expect(removed.lastRemoved).toMatchObject({ kind, label, index: 0 });
    const restored = run(removed, { type: 'undo' });
    expect(restored.draft).toEqual(s0.draft);
    expect(restored.deleted[key]).toEqual([]);
    expect(restored.lastRemoved).toBeNull();
  });

  it('puts a removed middle row back at its index', () => {
    const s0 = run(savedState(), { type: 'addFile', file: { id: 3, original_filename: '3.stl' } });
    const s = run(s0, { type: 'remove', kind: 'item', localId: 'i2' });
    expect(ids(s.draft.items)).toEqual(['i1', s0.draft.items[2].localId]);
    expect(ids(run(s, { type: 'undo' }).draft.items)).toEqual(ids(s0.draft.items));
  });

  it('does not queue a delete for a row that was never saved', () => {
    const s0 = run(savedState(), { type: 'addLink' });
    const s = run(s0, { type: 'remove', kind: 'link', localId: s0.draft.links[1].localId });
    expect(s.deleted.linkIds).toEqual([]);
    expect(s.lastRemoved).toMatchObject({ kind: 'link', label: 'link' });
  });

  it('labels a link by URL when it has no label, and an unnamed part generically', () => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addPart' });
    const newLink = s0.draft.links[1].localId;
    const withUrl = run(s0, { type: 'updateLink', localId: newLink, patch: { url: 'https://y.test' } }, { type: 'remove', kind: 'link', localId: newLink });
    expect(withUrl.lastRemoved?.label).toBe('https://y.test');
    expect(run(s0, { type: 'remove', kind: 'part', localId: s0.draft.parts[1].localId }).lastRemoved?.label).toBe('part');
  });

  it('keeps only the latest removal undoable', () => {
    const s = run(savedState(), { type: 'remove', kind: 'item', localId: 'i1' }, { type: 'remove', kind: 'part', localId: 'p1' }, { type: 'undo' });
    expect(ids(s.draft.parts)).toEqual(['p1']);
    expect(ids(s.draft.items)).toEqual(['i2']);
    expect(s.deleted).toEqual({ itemIds: [50], linkIds: [], partIds: [] });
  });

  it('ignores an unknown row, and undo/dismiss with nothing removed', () => {
    const s0 = savedState();
    expect(run(s0, { type: 'remove', kind: 'item', localId: 'nope' })).toBe(s0);
    expect(run(s0, { type: 'undo' })).toBe(s0);
    expect(run(s0, { type: 'dismissUndo' })).toBe(s0);
  });

  it('dismissing drops the undo but keeps the removal (and its pending delete)', () => {
    const s = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' }, { type: 'dismissUndo' });
    expect(s.lastRemoved).toBeNull();
    expect(s.draft.links).toEqual([]);
    expect(s.deleted.linkIds).toEqual([70]);
  });
});

describe('synced / saved', () => {
  it('stamps a created row with its server id', () => {
    const s0 = run(savedState(), { type: 'addPart' });
    const localId = s0.draft.parts[1].localId;
    const s = run(s0, { type: 'synced', event: { type: 'created', kind: 'part', localId, id: 81 } });
    expect(s.draft.parts.map(p => p.serverId)).toEqual([80, 81]);
    expect(s.draft.items).toBe(s0.draft.items);
  });

  it('un-queues a delete once it has gone through', () => {
    const s = run(savedState(),
      { type: 'remove', kind: 'item', localId: 'i1' }, { type: 'remove', kind: 'item', localId: 'i2' },
      { type: 'synced', event: { type: 'deleted', kind: 'item', id: 50 } });
    expect(s.deleted.itemIds).toEqual([51]);
  });

  it('makes undo re-create (not update) a removed row whose delete went through', () => {
    const s = run(savedState(),
      { type: 'remove', kind: 'link', localId: 'l1' },
      { type: 'synced', event: { type: 'deleted', kind: 'link', id: 70 } },
      { type: 'undo' });
    expect(s.draft.links).toEqual([{ localId: 'l1', url: 'https://x.test', label: 'Spec', serverId: undefined }]);
    expect(s.deleted.linkIds).toEqual([]);
  });

  it.each(['item', 'part'] as const)('does the same for a removed %s', kind => {
    const localId = kind === 'item' ? 'i1' : 'p1';
    const id = kind === 'item' ? 50 : 80;
    const s = run(savedState(), { type: 'remove', kind, localId }, { type: 'synced', event: { type: 'deleted', kind, id } });
    const row = s.lastRemoved?.kind === 'item' ? s.lastRemoved.item : s.lastRemoved?.kind === 'part' ? s.lastRemoved.part : null;
    expect(row?.serverId).toBeUndefined();
  });

  it('leaves the undo alone when a different row is deleted', () => {
    const removed = run(savedState(), { type: 'remove', kind: 'item', localId: 'i1' });
    const s = run(removed, { type: 'synced', event: { type: 'deleted', kind: 'part', id: 50 } });
    expect(s.lastRemoved).toBe(removed.lastRemoved);
  });

  it('sets the clean baseline on save', () => {
    expect(run(savedState(), { type: 'saved', snap: 'S' }).cleanSnap).toBe('S');
  });
});
