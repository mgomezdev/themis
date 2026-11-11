import { describe, expect, it } from 'vitest';
import {
  builderReducer, currentSnapshot, initialBuilderState, isDirty,
  type BuilderAction, type BuilderState,
} from './projectBuilderState';
import { draftSnapshot, type LocalItem, type LocalLink, type LocalPart } from './projectDraft';
import type { Project } from '../api/projects';

const item = (localId: string, file_id: number, extra: Partial<LocalItem> = {}): LocalItem => ({
  localId, file_id, file_name: `${file_id}.stl`, quantity: 1, sort_order: 0,
  kind: 'stl', source_ref: null, pack_rule: 'split', group_id: null,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, ...extra,
});
const link = (localId: string, extra: Partial<LocalLink> = {}): LocalLink => ({ localId, url: '', label: '', ...extra });
const part = (localId: string, extra: Partial<LocalPart> = {}): LocalPart => ({
  localId, name: '', quantity: 1, allocated: false, unitCost: '', ...extra,
});

/** A state with saved rows: items i1 (50), i2 (51, a 3MF plate in group 2), i3 (52); link l1 (70); part p1 (80). */
function savedState(): BuilderState {
  const s = initialBuilderState();
  return {
    ...s,
    draft: {
      ...s.draft, name: 'Shelf',
      items: [
        item('i1', 1, { serverId: 50 }),
        item('i2', 2, { serverId: 51, sort_order: 1, kind: '3mf_plate', source_ref: '1', group_id: 2 }),
        item('i3', 3, { serverId: 52, sort_order: 2 }),
      ],
      links: [link('l1', { serverId: 70, url: 'https://x.test', label: 'Spec' })],
      parts: [part('p1', { serverId: 80, name: 'magnet' })],
    },
  };
}
const run = (s: BuilderState, ...actions: BuilderAction[]) => actions.reduce(builderReducer, s);
const ids = (rows: { localId: string }[]) => rows.map(r => r.localId);
const FILE = { id: 9, original_filename: 'New.stl', kind: 'stl' as const };

const PROJECT = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  price: null, customer_id: null, customer_name: null, stage: 'draft',
  items: [{ id: 50, project_id: 7, file_id: 1, file_name: '1.stl', quantity: 2, quantity_completed: 1, quantity_failed: 0,
            filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0,
            kind: 'stl', source_ref: null, pack_rule: 'split', group_id: 1 }],
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

describe('isDirty / currentSnapshot', () => {
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

  it('counts pending item deletes', () => {
    const s = run(savedState(), { type: 'removeItems', localIds: ['i1'] });
    expect(currentSnapshot(s)).toBe(draftSnapshot(s.draft, [50]));
  });
});

describe('loaded', () => {
  it('replaces the draft, baseline, deletions and undo, and keeps the server view', () => {
    const before = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' });
    const s = run(before, { type: 'loaded', project: PROJECT });
    expect(s.draft.name).toBe('Gridfinity');
    expect(s.draft.items[0]).toMatchObject({ serverId: 50, group_id: 1 });
    expect(s.cleanSnap).toBe(draftSnapshot(s.draft));
    expect(s.deleted).toEqual({ itemIds: [], linkIds: [], partIds: [] });
    expect(s.lastRemoved).toBeNull();
    expect(s.isDraftStage).toBe(true);
    expect(s.serverItems.get(50)?.quantity_completed).toBe(1);
  });

  it('is not a draft stage for any other stage, and promoting clears it', () => {
    expect(run(initialBuilderState(), { type: 'loaded', project: { ...PROJECT, stage: 'planning' } as Project }).isDraftStage).toBe(false);
    expect(run(initialBuilderState(), { type: 'loaded', project: PROJECT }, { type: 'promoted' }).isDraftStage).toBe(false);
  });
});

describe('header fields', () => {
  it('sets fields without touching the rest', () => {
    const s0 = savedState();
    const s = run(s0, { type: 'setFields', fields: { notes: 'rush', onHold: true } });
    expect(s.draft).toMatchObject({ name: 'Shelf', notes: 'rush', onHold: true });
    expect(s.draft.items).toBe(s0.draft.items);
    expect(s.deleted).toBe(s0.deleted);
  });
});

describe('addFile', () => {
  it('adds a new last row in the given group with no filament ask', () => {
    const s = run(savedState(), { type: 'addFile', file: FILE, groupId: 2, defaultGroupId: 1 });
    expect(s.draft.items).toHaveLength(4);
    expect(s.draft.items[3]).toEqual({
      localId: expect.any(String), file_id: 9, file_name: 'New.stl', quantity: 1, sort_order: 3, group_id: 2,
      kind: 'stl', source_ref: null, pack_rule: 'split',
      filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null,
    });
  });

  it.each([
    ['3mf', { kind: '3mf_plate', source_ref: '1', pack_rule: 'split' }],
    ['gcode', { kind: 'gcode', source_ref: '1', pack_rule: 'locked' }],
  ] as const)('takes a %s file\'s source and rule from its kind', (kind, expected) => {
    const s = run(savedState(), { type: 'addFile', file: { ...FILE, kind }, groupId: null, defaultGroupId: 1 });
    expect(s.draft.items[3]).toMatchObject(expected);
  });

  it('bumps an STL already in the same group instead of adding it again', () => {
    const s = run(savedState(), { type: 'addFile', file: { ...FILE, id: 3 }, groupId: null, defaultGroupId: 1 });
    expect(s.draft.items.map(i => i.quantity)).toEqual([1, 1, 2]);
  });

  it('treats the Default Group by id and as null alike', () => {
    const s = run(savedState(), { type: 'addFile', file: { ...FILE, id: 3 }, groupId: 1, defaultGroupId: 1 });
    expect(s.draft.items.map(i => i.quantity)).toEqual([1, 1, 2]);
  });

  it('adds a new row for the same STL in another group', () => {
    const s = run(savedState(), { type: 'addFile', file: { ...FILE, id: 3 }, groupId: 2, defaultGroupId: 1 });
    expect(s.draft.items).toHaveLength(4);
    expect(s.draft.items[2].quantity).toBe(1);
  });

  it('always adds a 3MF or gcode pick as its own row', () => {
    const s = run(savedState(), { type: 'addFile', file: { id: 2, original_filename: '2.3mf', kind: '3mf' }, groupId: 2, defaultGroupId: 1 });
    expect(s.draft.items).toHaveLength(4);
  });
});

describe('item edits', () => {
  it('updates one item by local id', () => {
    const s = run(savedState(), { type: 'updateItem', localId: 'i2', patch: { quantity: 4, source_ref: '2' } });
    expect(s.draft.items.map(i => i.quantity)).toEqual([1, 4, 1]);
    expect(s.draft.items[1].source_ref).toBe('2');
  });

  const ASK = { filament_manufacturer: 'Acme', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000' };

  it('applies one filament ask to every item, keeping only the ask fields', () => {
    const s = run(savedState(), { type: 'applyFilamentToAll', ask: { ...ASK, extra: 1 } as typeof ASK });
    for (const it of s.draft.items) expect(it).toMatchObject(ASK);
    expect(s.draft.items[0]).not.toHaveProperty('extra');
  });

  it.each<[string, BuilderAction, (it: LocalItem) => unknown, unknown[]]>([
    ['sets the ask of', { type: 'setItemsAsk', localIds: ['i1', 'i3'], ask: ASK }, it => it.filament_type, ['PLA', null, 'PLA']],
    ['moves', { type: 'moveItems', localIds: ['i1', 'i3'], groupId: 5 }, it => it.group_id, [5, 2, 5]],
    ['moves to the Default Group', { type: 'moveItems', localIds: ['i2'], groupId: null }, it => it.group_id, [null, null, null]],
    ['sets the quantity of', { type: 'setItemsQuantity', localIds: ['i2'], quantity: 7 }, it => it.quantity, [1, 7, 1]],
  ])('%s only the picked rows', (_what, action, field, expected) => {
    expect(run(savedState(), action).draft.items.map(field)).toEqual(expected);
  });

  it('sets a packing rule only on picked rows whose kind can use it', () => {
    const s = run(savedState(), { type: 'setItemsRule', localIds: ['i1', 'i2'], rule: 'together' });
    expect(s.draft.items.map(i => i.pack_rule)).toEqual(['split', 'together', 'split']);   // an STL can't be 'together'
    expect(run(savedState(), { type: 'setItemsRule', localIds: ['i1', 'i2'], rule: 'solo' }).draft.items.map(i => i.pack_rule))
      .toEqual(['solo', 'solo', 'split']);
  });

  it('moves the items of a group that no longer exists to the Default Group', () => {
    const s = run(savedState(), { type: 'moveItems', localIds: ['i3'], groupId: 9 }, { type: 'groupsListed', groupIds: [1, 9] });
    expect(s.draft.items.map(i => i.group_id)).toEqual([null, null, 9]);
  });

  it('leaves the state alone when every group is still there', () => {
    const s0 = savedState();
    expect(run(s0, { type: 'groupsListed', groupIds: [1, 2] })).toBe(s0);
  });
});

describe('links and parts', () => {
  it('adds a blank link and a blank part (quantity 1) with fresh local ids', () => {
    const s = run(savedState(), { type: 'addLink' }, { type: 'addPart' });
    expect(s.draft.links[1]).toEqual({ localId: expect.any(String), url: '', label: '' });
    expect(s.draft.parts[1]).toEqual({ localId: expect.any(String), name: '', quantity: 1, allocated: false, unitCost: '' });
    expect(s.draft.links[1].localId).not.toBe(s.draft.parts[1].localId);
  });

  it('updates one link or part by local id', () => {
    const s = run(savedState(), { type: 'updateLink', localId: 'l1', patch: { label: 'New' } }, { type: 'updatePart', localId: 'p1', patch: { unitCost: '2' } });
    expect(s.draft.links[0]).toMatchObject({ label: 'New', url: 'https://x.test', serverId: 70 });
    expect(s.draft.parts[0]).toMatchObject({ unitCost: '2', name: 'magnet', serverId: 80 });
  });
});

describe('remove / undo', () => {
  it('removes several items at once, queues their deletes, and undo restores each in place', () => {
    const s0 = savedState();
    const s = run(s0, { type: 'removeItems', localIds: ['i1', 'i3'] });
    expect(ids(s.draft.items)).toEqual(['i2']);
    expect(s.deleted.itemIds).toEqual([50, 52]);
    expect(s.lastRemoved).toMatchObject({ kind: 'item', label: '2 items' });
    const back = run(s, { type: 'undo' });
    expect(back.draft.items).toEqual(s0.draft.items);
    expect(back.deleted.itemIds).toEqual([]);
    expect(back.lastRemoved).toBeNull();
  });

  it('restores adjacent removed items in their original order', () => {
    const s0 = savedState();
    const s = run(s0, { type: 'removeItems', localIds: ['i1', 'i2'] }, { type: 'undo' });
    expect(ids(s.draft.items)).toEqual(['i1', 'i2', 'i3']);
  });

  it('labels a single removed item by its file name', () => {
    expect(run(savedState(), { type: 'removeItems', localIds: ['i2'] }).lastRemoved?.label).toBe('2.stl');
  });

  it('undo only un-queues the deletes of the rows it restores', () => {
    const s = run(savedState(), { type: 'removeItems', localIds: ['i1'] }, { type: 'dismissUndo' }, { type: 'removeItems', localIds: ['i2'] }, { type: 'undo' });
    expect(s.deleted.itemIds).toEqual([50]);
    expect(ids(s.draft.items)).toEqual(['i2', 'i3']);
  });

  it.each([
    ['link', 'l1', 'linkIds', 70, 'Spec'],
    ['part', 'p1', 'partIds', 80, 'magnet'],
  ] as const)('removing a saved %s queues its delete; undo restores it in place and un-queues it', (kind, localId, key, id, label) => {
    const s0 = savedState();
    const removed = run(s0, { type: 'remove', kind, localId });
    expect(removed.deleted[key]).toEqual([id]);
    expect(removed.lastRemoved).toMatchObject({ kind, label, index: 0 });
    const restored = run(removed, { type: 'undo' });
    expect(restored.draft).toEqual(s0.draft);
    expect(restored.deleted[key]).toEqual([]);
  });

  it('puts a removed middle link back at its index', () => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addLink' });
    const middle = s0.draft.links[1].localId;
    const s = run(s0, { type: 'remove', kind: 'link', localId: middle }, { type: 'undo' });
    expect(ids(s.draft.links)).toEqual(ids(s0.draft.links));
  });

  it('does not queue a delete for a row that was never saved', () => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addPart' }, { type: 'addFile', file: FILE, groupId: null, defaultGroupId: null });
    const s = run(s0,
      { type: 'remove', kind: 'link', localId: s0.draft.links[1].localId },
      { type: 'remove', kind: 'part', localId: s0.draft.parts[1].localId },
      { type: 'removeItems', localIds: [s0.draft.items[3].localId] });
    expect(s.deleted).toEqual({ itemIds: [], linkIds: [], partIds: [] });
  });

  it('labels a link by URL without a label, a blank link and an unnamed part generically', () => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addPart' });
    const newLink = s0.draft.links[1].localId;
    expect(run(s0, { type: 'remove', kind: 'link', localId: newLink }).lastRemoved?.label).toBe('link');
    const withUrl = run(s0, { type: 'updateLink', localId: newLink, patch: { url: 'https://y.test' } }, { type: 'remove', kind: 'link', localId: newLink });
    expect(withUrl.lastRemoved?.label).toBe('https://y.test');
    expect(run(s0, { type: 'remove', kind: 'part', localId: s0.draft.parts[1].localId }).lastRemoved?.label).toBe('part');
  });

  it('ignores unknown rows, and undo/dismiss with nothing removed', () => {
    const s0 = savedState();
    expect(run(s0, { type: 'removeItems', localIds: ['nope'] })).toBe(s0);
    expect(run(s0, { type: 'remove', kind: 'link', localId: 'nope' })).toBe(s0);
    expect(run(s0, { type: 'remove', kind: 'part', localId: 'nope' })).toBe(s0);
    expect(run(s0, { type: 'undo' })).toBe(s0);
    expect(run(s0, { type: 'dismissUndo' })).toBe(s0);
  });

  it('dismissing drops the undo but keeps the removal and its pending delete', () => {
    const s = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' }, { type: 'dismissUndo' });
    expect(s.lastRemoved).toBeNull();
    expect(s.draft.links).toEqual([]);
    expect(s.deleted.linkIds).toEqual([70]);
  });
});

describe('synced / saved', () => {
  it.each(['item', 'link', 'part'] as const)('stamps a created %s with its server id', kind => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addPart' }, { type: 'addFile', file: FILE, groupId: null, defaultGroupId: null });
    const rows = kind === 'item' ? s0.draft.items : kind === 'link' ? s0.draft.links : s0.draft.parts;
    const localId = rows[rows.length - 1].localId;
    const s = run(s0, { type: 'synced', event: { type: 'created', kind, localId, id: 99 } });
    const after = kind === 'item' ? s.draft.items : kind === 'link' ? s.draft.links : s.draft.parts;
    expect(after.map(r => r.serverId).slice(-1)).toEqual([99]);
    expect(after.slice(0, -1)).toEqual(rows.slice(0, -1));
  });

  it.each([
    ['item', { type: 'removeItems', localIds: ['i1', 'i3'] }, 50, 'itemIds', [52]],
    ['link', { type: 'remove', kind: 'link', localId: 'l1' }, 70, 'linkIds', []],
    ['part', { type: 'remove', kind: 'part', localId: 'p1' }, 80, 'partIds', []],
  ] as const)('un-queues a %s delete once it has gone through', (kind, remove, id, key, left) => {
    const s = run(savedState(), remove as BuilderAction, { type: 'synced', event: { type: 'deleted', kind, id } });
    expect(s.deleted[key]).toEqual(left);
  });

  it('makes undo re-create (not update) removed rows whose delete went through', () => {
    const items = run(savedState(), { type: 'removeItems', localIds: ['i1', 'i3'] },
      { type: 'synced', event: { type: 'deleted', kind: 'item', id: 50 } }, { type: 'undo' });
    expect(items.draft.items.map(i => i.serverId)).toEqual([undefined, 51, 52]);
    expect(items.deleted.itemIds).toEqual([]);                    // i3 is back too, so its delete is off

    const link = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' },
      { type: 'synced', event: { type: 'deleted', kind: 'link', id: 70 } }, { type: 'undo' });
    expect(link.draft.links[0]).toMatchObject({ localId: 'l1', url: 'https://x.test', serverId: undefined });

    const part = run(savedState(), { type: 'remove', kind: 'part', localId: 'p1' },
      { type: 'synced', event: { type: 'deleted', kind: 'part', id: 80 } }, { type: 'undo' });
    expect(part.draft.parts[0]).toMatchObject({ localId: 'p1', serverId: undefined });
  });

  it('leaves the undo alone when a different row is deleted', () => {
    const removed = run(savedState(), { type: 'removeItems', localIds: ['i1'] });
    for (const e of [{ kind: 'part', id: 50 }, { kind: 'item', id: 52 }] as const) {
      expect(run(removed, { type: 'synced', event: { type: 'deleted', ...e } }).lastRemoved).toBe(removed.lastRemoved);
    }
    const link = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' });
    expect(run(link, { type: 'synced', event: { type: 'deleted', kind: 'link', id: 71 } }).lastRemoved).toBe(link.lastRemoved);
    const part = run(savedState(), { type: 'remove', kind: 'part', localId: 'p1' });
    expect(run(part, { type: 'synced', event: { type: 'deleted', kind: 'part', id: 81 } }).lastRemoved).toBe(part.lastRemoved);
  });

  it('clears the id of a restored row whose delete lands after the undo (undo during the save)', () => {
    const s = run(savedState(), { type: 'remove', kind: 'link', localId: 'l1' }, { type: 'undo' },
      { type: 'synced', event: { type: 'deleted', kind: 'link', id: 70 } });
    expect(s.draft.links[0]).toMatchObject({ localId: 'l1', serverId: undefined });
    const items = run(savedState(), { type: 'removeItems', localIds: ['i2'] }, { type: 'undo' },
      { type: 'synced', event: { type: 'deleted', kind: 'item', id: 51 } });
    expect(items.draft.items.map(i => i.serverId)).toEqual([50, undefined, 52]);
    const parts = run(savedState(), { type: 'remove', kind: 'part', localId: 'p1' }, { type: 'undo' },
      { type: 'synced', event: { type: 'deleted', kind: 'part', id: 80 } });
    expect(parts.draft.parts[0].serverId).toBeUndefined();
  });

  it.each(['item', 'link', 'part'] as const)('queues the delete of a %s created after it was removed, and undo keeps it', kind => {
    const s0 = run(savedState(), { type: 'addLink' }, { type: 'addPart' }, { type: 'addFile', file: FILE, groupId: null, defaultGroupId: null });
    const rows = kind === 'item' ? s0.draft.items : kind === 'link' ? s0.draft.links : s0.draft.parts;
    const localId = rows[rows.length - 1].localId;
    const remove: BuilderAction = kind === 'item' ? { type: 'removeItems', localIds: [localId] } : { type: 'remove', kind, localId };
    const key = kind === 'item' ? 'itemIds' : kind === 'link' ? 'linkIds' : 'partIds';
    const s = run(s0, remove, { type: 'synced', event: { type: 'created', kind, localId, id: 99 } });
    expect(s.deleted[key]).toEqual([99]);                       // the server row the draft no longer has
    const back = run(s, { type: 'undo' });
    expect(back.deleted[key]).toEqual([]);
    const restored = kind === 'item' ? back.draft.items : kind === 'link' ? back.draft.links : back.draft.parts;
    expect(restored.find(r => r.localId === localId)?.serverId).toBe(99);   // updated next save, not created again
  });

  it('queues the delete of a created row that was removed and whose undo was dismissed', () => {
    const s0 = run(savedState(), { type: 'addLink' });
    const localId = s0.draft.links[1].localId;
    const s = run(s0, { type: 'remove', kind: 'link', localId }, { type: 'dismissUndo' },
      { type: 'synced', event: { type: 'created', kind: 'link', localId, id: 99 } });
    expect(s.deleted.linkIds).toEqual([99]);
  });

  it('counts removals: only a new removal moves the undo clock', () => {
    const s0 = savedState();
    const r1 = run(s0, { type: 'remove', kind: 'link', localId: 'l1' });
    expect(r1.removalSeq).toBe(s0.removalSeq + 1);
    const synced = run(r1, { type: 'synced', event: { type: 'deleted', kind: 'link', id: 70 } });
    expect(synced.removalSeq).toBe(r1.removalSeq);
    expect(run(synced, { type: 'removeItems', localIds: ['i1'] }).removalSeq).toBe(r1.removalSeq + 1);
    expect(run(s0, { type: 'removeItems', localIds: ['nope'] }).removalSeq).toBe(s0.removalSeq);
  });

  it('sets the clean baseline on save', () => {
    expect(run(savedState(), { type: 'saved', snap: 'S' }).cleanSnap).toBe('S');
  });
});
