import { afterEach, describe, expect, it, vi } from 'vitest';
import { NO_DELETIONS, projectFieldsOf, syncProject, type SyncEvent } from './projectSync';
import { Reply, stubFetch } from '../test/fetchStub';
import type { LocalItem, LocalLink, LocalPart, ProjectDraft } from '../lib/projectDraft';

const item = (localId: string, file_id: number, extra: Partial<LocalItem> = {}): LocalItem => ({
  localId, file_id, file_name: `${file_id}.stl`, quantity: 1, sort_order: 0,
  kind: 'stl', source_ref: null, pack_rule: 'split', group_id: null,
  filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, ...extra,
});
const link = (localId: string, url: string, extra: Partial<LocalLink> = {}): LocalLink => ({ localId, url, label: '', ...extra });
const part = (localId: string, name: string, extra: Partial<LocalPart> = {}): LocalPart => ({
  localId, name, quantity: 1, allocated: false, unitCost: '', ...extra,
});
const DRAFT: ProjectDraft = {
  name: 'Shelf', customer: 'Acme', customerId: 3, orderType: 'customer', onHold: true,
  dueDate: '2026-12-01', notes: 'rush', price: '40', items: [], links: [], parts: [],
};
const P = 7;
const writes = (api: ReturnType<typeof stubFetch>) => api.calls.map(c => `${c.method} ${c.url}`);

afterEach(() => vi.unstubAllGlobals());

describe('projectFieldsOf', () => {
  it('sends every header field of a customer project', () => {
    expect(projectFieldsOf(DRAFT)).toEqual({
      name: 'Shelf', customer: 'Acme', order_type: 'customer', on_hold: true, due_date: '2026-12-01',
      notes: 'rush', price: 40, customer_id: 3,
    });
  });

  it('drops the customer name and account of an internal project', () => {
    expect(projectFieldsOf({ ...DRAFT, orderType: 'internal' })).toMatchObject({ order_type: 'internal', customer: '', customer_id: null });
  });

  it('sends empty optional fields as null', () => {
    expect(projectFieldsOf({ ...DRAFT, dueDate: '', notes: '', price: '  ' }))
      .toMatchObject({ due_date: null, notes: null, price: null });
  });

  it('keeps a zero price', () => {
    expect(projectFieldsOf({ ...DRAFT, price: '0' }).price).toBe(0);
  });
});

describe('syncProject - new project', () => {
  const routes = {
    'POST /api/v1/projects': { id: P },
    'POST /api/v1/projects/7/items': (c: { body: { file_id: number } }) => ({ id: 100 + c.body.file_id }),
    'POST /api/v1/projects/7/links': { id: 70 },
    'POST /api/v1/projects/7/parts': { id: 80 },
  };

  it('creates the project, then its items (numbered by position), links and parts, and returns its id', async () => {
    const api = stubFetch(routes);
    const d = {
      ...DRAFT,
      items: [item('a', 1, { sort_order: 5, quantity: 3, group_id: 9 }),
              item('b', 2, { sort_order: 0, filament_color: '#FF0000', kind: '3mf_plate', source_ref: '2', pack_rule: 'together' })],
      links: [link('c', 'https://x.test', { label: 'Spec' }), link('d', 'https://y.test')],
      parts: [part('e', 'magnet', { unitCost: '0.25', allocated: true }), part('f', 'screw')],
    };
    expect(await syncProject(null, d, NO_DELETIONS)).toBe(P);
    expect(writes(api)).toEqual([
      'POST /api/v1/projects', 'POST /api/v1/projects/7/items', 'POST /api/v1/projects/7/items',
      'POST /api/v1/projects/7/links', 'POST /api/v1/projects/7/links',
      'POST /api/v1/projects/7/parts', 'POST /api/v1/projects/7/parts',
    ]);
    expect(api.calls[0].body).toEqual(projectFieldsOf(d));
    expect(api.to('POST', '/api/v1/projects/7/items').map(c => c.body)).toEqual([
      // No group_id: a new project has only its Default Group.
      { file_id: 1, quantity: 3, filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 0,
        kind: 'stl', source_ref: null, pack_rule: 'split' },
      { file_id: 2, quantity: 1, filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: '#FF0000', sort_order: 1,
        kind: '3mf_plate', source_ref: '2', pack_rule: 'together' },
    ]);
    expect(api.to('POST', '/api/v1/projects/7/links').map(c => c.body)).toEqual([
      { url: 'https://x.test', label: 'Spec' }, { url: 'https://y.test', label: null },
    ]);
    expect(api.to('POST', '/api/v1/projects/7/parts').map(c => c.body)).toEqual([
      { name: 'magnet', quantity: 1, allocated: true, unit_cost: 0.25 },
      { name: 'screw', quantity: 1, allocated: false },                    // blank cost left out, not null
    ]);
  });

  it('reports each created row with its new id, in order', async () => {
    stubFetch(routes);
    const events: SyncEvent[] = [];
    await syncProject(null, { ...DRAFT, items: [item('a', 1), item('b', 2)], links: [link('c', 'u')], parts: [part('e', 'p')] },
      NO_DELETIONS, e => events.push(e));
    expect(events).toEqual([
      { type: 'created', kind: 'item', localId: 'a', id: 101 },
      { type: 'created', kind: 'item', localId: 'b', id: 102 },
      { type: 'created', kind: 'link', localId: 'c', id: 70 },
      { type: 'created', kind: 'part', localId: 'e', id: 80 },
    ]);
  });

  it('creates a project when the id is not a real one (a malformed route)', async () => {
    const api = stubFetch(routes);
    expect(await syncProject(NaN, DRAFT, NO_DELETIONS)).toBe(P);
    expect(writes(api)).toEqual(['POST /api/v1/projects']);
  });

  it('ignores pending deletions (a new project has no saved rows)', async () => {
    const api = stubFetch(routes);
    await syncProject(null, DRAFT, { itemIds: [1], linkIds: [2], partIds: [3] });
    expect(writes(api)).toEqual(['POST /api/v1/projects']);
  });
});

describe('syncProject - existing project', () => {
  const routes = {
    'PATCH /api/v1/projects/7': { id: P },
    'DELETE /api/v1/projects/7/items/51': { deleted: 51 },
    'PUT /api/v1/projects/7/items/50': { id: 50 },
    'POST /api/v1/projects/7/items': { id: 60 },
    'DELETE /api/v1/projects/7/links/71': { deleted: 71 },
    'PUT /api/v1/projects/7/links/70': { id: 70 },
    'POST /api/v1/projects/7/links': { id: 72 },
    'DELETE /api/v1/projects/7/parts/81': { deleted: 81 },
    'PUT /api/v1/projects/7/parts/80': { id: 80 },
    'POST /api/v1/projects/7/parts': { id: 82 },
  };
  const DELETED = { itemIds: [51], linkIds: [71], partIds: [81] };
  const EDITED: ProjectDraft = {
    ...DRAFT,
    // A new item listed before a saved one: saved rows are still updated first.
    items: [item('n', 2, { sort_order: 4, group_id: 3, kind: 'gcode', source_ref: '1', pack_rule: 'locked' }),
            item('s', 1, { serverId: 50, quantity: 2, sort_order: 0, pack_rule: 'solo', kind: '3mf_object', source_ref: 'o1' })],
    // New links/parts listed first: unlike items, they are updated or added row by row, in order.
    links: [link('n', 'https://new.test'), link('s', 'https://old.test', { serverId: 70, label: 'Spec' })],
    parts: [part('n', 'screw', { unitCost: '1.5' }), part('s', 'magnet', { serverId: 80, unitCost: '' })],
  };

  it('patches the project, then per kind deletes removed rows, updates saved ones and adds new ones', async () => {
    const api = stubFetch(routes);
    expect(await syncProject(P, EDITED, DELETED)).toBe(P);
    expect(writes(api)).toEqual([
      'PATCH /api/v1/projects/7',
      'DELETE /api/v1/projects/7/items/51', 'PUT /api/v1/projects/7/items/50', 'POST /api/v1/projects/7/items',
      'DELETE /api/v1/projects/7/links/71', 'POST /api/v1/projects/7/links', 'PUT /api/v1/projects/7/links/70',
      'DELETE /api/v1/projects/7/parts/81', 'POST /api/v1/projects/7/parts', 'PUT /api/v1/projects/7/parts/80',
    ]);
    expect(api.calls[0].body).toEqual(projectFieldsOf(EDITED));
  });

  it('keeps each item\'s own sort order, sends the full filament ask, its rule and group, and its source on create only', async () => {
    const api = stubFetch(routes);
    await syncProject(P, EDITED, NO_DELETIONS);
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).toEqual({
      quantity: 2, filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 0,
      pack_rule: 'solo',                                  // no group_id: it's in the Default Group
    });
    expect(api.to('POST', '/api/v1/projects/7/items')[0].body).toEqual({
      file_id: 2, quantity: 1, filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 4,
      kind: 'gcode', source_ref: '1', pack_rule: 'locked', group_id: 3,
    });
  });

  it('sends a saved item\'s group when it has one', async () => {
    const api = stubFetch(routes);
    await syncProject(P, { ...EDITED, items: [item('s', 1, { serverId: 50, group_id: 2 })] }, NO_DELETIONS);
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).toMatchObject({ group_id: 2 });
  });

  it('does not report a create whose reply has no numeric id', async () => {
    stubFetch({ ...routes, 'POST /api/v1/projects/7/links': {}, 'POST /api/v1/projects/7/parts': { id: '82' } });
    const events: SyncEvent[] = [];
    await syncProject(P, { ...DRAFT, links: [link('n', 'u')], parts: [part('n', 'p')] }, NO_DELETIONS, e => events.push(e));
    expect(events).toEqual([]);
  });

  it('clears a saved part\'s blank unit cost with null, but leaves it out of a new part', async () => {
    const api = stubFetch(routes);
    await syncProject(P, { ...EDITED, parts: [part('s', 'magnet', { serverId: 80 }), part('n', 'screw')] }, NO_DELETIONS);
    expect(api.to('PUT', '/api/v1/projects/7/parts/80')[0].body).toEqual({ name: 'magnet', quantity: 1, allocated: false, unit_cost: null });
    expect(api.to('POST', '/api/v1/projects/7/parts')[0].body).toEqual({ name: 'screw', quantity: 1, allocated: false });
  });

  it('sends a link\'s blank label as null on update and create', async () => {
    const api = stubFetch(routes);
    await syncProject(P, { ...EDITED, links: [link('s', 'https://old.test', { serverId: 70 }), link('n', 'https://new.test')] }, NO_DELETIONS);
    expect(api.to('PUT', '/api/v1/projects/7/links/70')[0].body).toEqual({ url: 'https://old.test', label: null });
    expect(api.to('POST', '/api/v1/projects/7/links')[0].body).toEqual({ url: 'https://new.test', label: null });
  });

  it('reports every delete and create as it happens, and nothing for updates', async () => {
    stubFetch(routes);
    const events: SyncEvent[] = [];
    await syncProject(P, EDITED, DELETED, e => events.push(e));
    expect(events).toEqual([
      { type: 'deleted', kind: 'item', id: 51 },
      { type: 'created', kind: 'item', localId: 'n', id: 60 },
      { type: 'deleted', kind: 'link', id: 71 },
      { type: 'created', kind: 'link', localId: 'n', id: 72 },
      { type: 'deleted', kind: 'part', id: 81 },
      { type: 'created', kind: 'part', localId: 'n', id: 82 },
    ]);
  });

  it('stops at the first failure, having reported only what succeeded before it', async () => {
    const api = stubFetch({ ...routes, 'POST /api/v1/projects/7/links': new Reply(500, 'disk full') });
    const events: SyncEvent[] = [];
    await expect(syncProject(P, EDITED, DELETED, e => events.push(e))).rejects.toThrow();
    expect(events.map(e => `${e.type} ${e.kind}`)).toEqual(['deleted item', 'created item', 'deleted link']);
    expect(writes(api).slice(-1)[0]).toBe('POST /api/v1/projects/7/links');
    expect(api.to('DELETE', '/api/v1/projects/7/parts/81')).toEqual([]);
  });

  it('does not report a delete that failed', async () => {
    stubFetch({ ...routes, 'DELETE /api/v1/projects/7/items/51': new Reply(404, 'gone') });
    const events: SyncEvent[] = [];
    await expect(syncProject(P, EDITED, DELETED, e => events.push(e))).rejects.toThrow();
    expect(events).toEqual([]);
  });

  it('only patches when nothing else changed', async () => {
    const api = stubFetch(routes);
    await syncProject(P, DRAFT, NO_DELETIONS);
    expect(writes(api)).toEqual(['PATCH /api/v1/projects/7']);
  });
});
