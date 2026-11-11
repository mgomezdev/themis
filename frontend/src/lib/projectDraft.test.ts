import { describe, expect, it } from 'vitest';
import {
  GENERATE_ERRORS, askOf, draftSnapshot, newLocalId, parseGenerateError, projectToDraft, unitCostValue,
  type LoadedProject, type ProjectDraft,
} from './projectDraft';

const ITEM = {
  localId: 'L1', serverId: 50, file_id: 1, file_name: 'Bracket.stl', quantity: 2, sort_order: 0,
  filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null,
};
const LINK = { localId: 'L2', serverId: 70, url: 'https://example.test', label: 'Spec' };
const PART = { localId: 'L3', serverId: 80, name: 'magnet', quantity: 4, allocated: false, unitCost: '0.25' };
const DRAFT: ProjectDraft = {
  name: 'Gridfinity', customer: 'Acme', customerId: 3, orderType: 'customer', onHold: false,
  dueDate: '2026-12-01', notes: 'rush', price: '40', items: [ITEM], links: [LINK], parts: [PART],
};

describe('askOf', () => {
  it('keeps exactly the four filament-ask fields', () => {
    expect(askOf(ITEM)).toEqual({
      filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null,
    });
  });
});

describe('unitCostValue', () => {
  it.each([
    ['', null],
    ['   ', null],
    ['0', 0],
    ['0.25', 0.25],
    [' 3 ', 3],
  ])('%j -> %j', (unitCost, expected) => {
    expect(unitCostValue({ unitCost })).toBe(expected);
  });
});

describe('newLocalId', () => {
  it('never repeats', () => {
    const ids = Array.from({ length: 50 }, newLocalId);
    expect(new Set(ids).size).toBe(50);
  });
});

describe('draftSnapshot', () => {
  it('is the same for equal drafts, whatever their local ids', () => {
    const relabelled = {
      ...DRAFT,
      items: [{ ...ITEM, localId: 'X' }], links: [{ ...LINK, localId: 'Y' }], parts: [{ ...PART, localId: 'Z' }],
    };
    expect(draftSnapshot(relabelled)).toBe(draftSnapshot(DRAFT));
  });

  it.each<[string, Partial<ProjectDraft>]>([
    ['name', { name: 'Other' }],
    ['customer', { customer: 'Beta' }],
    ['customer account', { customerId: 4 }],
    ['order type', { orderType: 'internal' }],
    ['on hold', { onHold: true }],
    ['due date', { dueDate: '' }],
    ['notes', { notes: '' }],
    ['price', { price: '41' }],
    ['item file', { items: [{ ...ITEM, file_id: 2 }] }],
    ['item quantity', { items: [{ ...ITEM, quantity: 3 }] }],
    ['item manufacturer', { items: [{ ...ITEM, filament_manufacturer: 'Acme' }] }],
    ['item type', { items: [{ ...ITEM, filament_type: 'PETG' }] }],
    ['item subtype', { items: [{ ...ITEM, filament_subtype: 'silk' }] }],
    ['item colour', { items: [{ ...ITEM, filament_color: '#FF0000' }] }],
    ['item order', { items: [{ ...ITEM, sort_order: 1 }] }],
    ['an added item', { items: [ITEM, { ...ITEM, localId: 'L9', file_id: 2 }] }],
    ['a removed item', { items: [] }],
    ['link url', { links: [{ ...LINK, url: 'https://other.test' }] }],
    ['link label', { links: [{ ...LINK, label: '' }] }],
    ['link saved-ness', { links: [{ ...LINK, serverId: undefined }] }],
    ['a removed link', { links: [] }],
    ['part name', { parts: [{ ...PART, name: 'screw' }] }],
    ['part quantity', { parts: [{ ...PART, quantity: 5 }] }],
    ['part allocated', { parts: [{ ...PART, allocated: true }] }],
    ['part unit cost', { parts: [{ ...PART, unitCost: '' }] }],
    ['part saved-ness', { parts: [{ ...PART, serverId: undefined }] }],
    ['a removed part', { parts: [] }],
  ])('changes with the %s', (_field, change) => {
    expect(draftSnapshot({ ...DRAFT, ...change })).not.toBe(draftSnapshot(DRAFT));
  });

  it('ignores the customer name and account of an internal project (Save drops them)', () => {
    const internal = { ...DRAFT, orderType: 'internal' as const };
    expect(draftSnapshot({ ...internal, customer: 'Beta', customerId: 9 })).toBe(draftSnapshot(internal));
    expect(draftSnapshot({ ...internal, customer: '', customerId: null })).toBe(draftSnapshot(internal));
  });

  it('round-trips a loaded project: the loaded draft fingerprints like its own copy', () => {
    const d = projectToDraft(LOADED);
    expect(draftSnapshot(projectToDraft(LOADED))).toBe(draftSnapshot(d));
  });
});

const LOADED: LoadedProject = {
  name: 'Gridfinity', customer: 'typed name', order_type: 'internal', on_hold: true, due_date: '2026-12-01',
  notes: 'rush', price: 40, customer_id: null, customer_name: null,
  items: [{
    id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 2, quantity_completed: 1, quantity_failed: 0,
    filament_manufacturer: 'Acme', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000', sort_order: 3,
  } as LoadedProject['items'][number]],
  links: [{ id: 70, project_id: 7, url: 'https://example.test', label: null, sort_order: 0, created_at: '' }],
  parts: [
    { id: 80, project_id: 7, name: 'magnet', quantity: 4, allocated: true, sort_order: 0, created_at: '', unit_cost: 0.25 },
    { id: 81, project_id: 7, name: 'screw', quantity: 1, allocated: false, sort_order: 1, created_at: '', unit_cost: null },
  ],
};

describe('projectToDraft', () => {
  it('maps every field, keeping server ids and dropping server-only ones', () => {
    const d = projectToDraft(LOADED);
    expect({ ...d, items: [], links: [], parts: [] }).toEqual({
      name: 'Gridfinity', customer: 'typed name', customerId: null, orderType: 'internal', onHold: true,
      dueDate: '2026-12-01', notes: 'rush', price: '40', items: [], links: [], parts: [],
    });
    expect(d.items).toEqual([{
      localId: expect.any(String), serverId: 50, file_id: 1, file_name: 'Bracket.stl', quantity: 2, sort_order: 3,
      filament_manufacturer: 'Acme', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000',
    }]);
    expect(d.links).toEqual([{ localId: expect.any(String), serverId: 70, url: 'https://example.test', label: '' }]);
    expect(d.parts).toEqual([
      { localId: expect.any(String), serverId: 80, name: 'magnet', quantity: 4, allocated: true, unitCost: '0.25' },
      { localId: expect.any(String), serverId: 81, name: 'screw', quantity: 1, allocated: false, unitCost: '' },
    ]);
  });

  it('gives every row a distinct local id', () => {
    const d = projectToDraft(LOADED);
    const ids = [...d.items, ...d.links, ...d.parts].map(r => r.localId);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('turns absent optional fields into empty form values', () => {
    const d = projectToDraft({
      ...LOADED, due_date: null, notes: null, price: null, customer: null as unknown as string,
      on_hold: undefined as unknown as boolean, order_type: undefined as unknown as string, links: undefined, parts: null,
    });
    expect(d).toMatchObject({ dueDate: '', notes: '', price: '', customer: '', onHold: false, orderType: 'internal', links: [], parts: [] });
  });

  it('keeps a zero price (it is a quote, not "no price")', () => {
    expect(projectToDraft({ ...LOADED, price: 0 }).price).toBe('0');
  });

  it('treats a project linked to a customer account as a customer project, whatever its stored type', () => {
    const d = projectToDraft({ ...LOADED, order_type: 'internal', customer_id: 3, customer_name: 'Acme Ltd' });
    expect(d).toMatchObject({ orderType: 'customer', customerId: 3, customer: 'Acme Ltd' });
  });

  it('shows the typed customer name when the linked account has no name', () => {
    expect(projectToDraft({ ...LOADED, customer_id: 3, customer_name: null }).customer).toBe('typed name');
  });

  it('keeps the typed customer name for an unlinked project even if a stale account name came back', () => {
    expect(projectToDraft({ ...LOADED, order_type: 'customer', customer_id: null, customer_name: 'Old Co' }))
      .toMatchObject({ orderType: 'customer', customer: 'typed name' });
  });
});

describe('parseGenerateError', () => {
  it.each(Object.entries(GENERATE_ERRORS))('words a %s', (code, wording) => {
    expect(parseGenerateError(`${code} whatever the server said`)).toBe(wording);
  });

  it.each(['400 STL not found', '400 missing file foo.stl'])('says STL files are missing for %j', msg => {
    expect(parseGenerateError(msg)).toBe('One or more STL files are missing. Remove and re-add them.');
  });

  it('prefers the status wording over an STL mention', () => {
    expect(parseGenerateError('502 STL upstream')).toBe(GENERATE_ERRORS[502]);
  });

  it.each(['409 project is a draft', 'network down', ''])('passes anything else through: %j', msg => {
    expect(parseGenerateError(msg)).toBe(`Generation failed: ${msg}`);
  });
});
