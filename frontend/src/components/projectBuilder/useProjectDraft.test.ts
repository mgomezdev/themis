import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { UNDO_TIMEOUT_MS, useProjectDraft } from './useProjectDraft';
import { Reply, stubFetch } from '../../test/fetchStub';

const PROJECT = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  price: null, customer_id: null, customer_name: null, stage: 'planning', links: [], parts: [],
  items: [{ id: 50, project_id: 7, file_id: 1, file_name: '1.stl', quantity: 2, quantity_completed: 0, quantity_failed: 0,
            filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0 }],
};

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function loadedHook(routes: Record<string, unknown> = {}) {
  const api = stubFetch({ 'GET /api/v1/projects/7': PROJECT, 'PATCH /api/v1/projects/7': PROJECT, ...routes });
  const hook = renderHook(() => useProjectDraft(7, null));
  await waitFor(() => expect(hook.result.current.state.draft.name).toBe('Gridfinity'));
  return { api, ...hook };
}

describe('useProjectDraft', () => {
  it('starts a new project (pre-linked to a customer) without fetching anything', () => {
    const api = stubFetch({});
    const { result } = renderHook(() => useProjectDraft(null, 3));
    expect(result.current.state.draft).toMatchObject({ customerId: 3, orderType: 'customer' });
    expect(result.current.isDirty).toBe(true);
    expect(api.calls).toEqual([]);
  });

  it('loads an existing project clean', async () => {
    const { result } = await loadedHook();
    expect(result.current.isDirty).toBe(false);
    expect(result.current.state.serverItems.has(50)).toBe(true);
  });

  it('ignores a load that finishes after unmount', async () => {
    let resolve!: (v: unknown) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise(r => { resolve = r; })));
    const { result, unmount } = renderHook(() => useProjectDraft(7, null));
    const before = result.current.state;
    unmount();
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => { resolve({ ok: true, status: 200, json: () => Promise.resolve(PROJECT) }); });
    expect(result.current.state).toBe(before);
    expect(err).not.toHaveBeenCalled();
  });

  it('ignores a slow load for a project it has since moved off', async () => {
    const pending: Record<string, (v: unknown) => void> = {};
    vi.stubGlobal('fetch', vi.fn((url: string) => new Promise(r => { pending[url] = r; })));
    const reply = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });
    const { result, rerender } = renderHook(({ id }) => useProjectDraft(id, null), { initialProps: { id: 7 } });
    rerender({ id: 8 });
    await act(async () => { pending['/api/v1/projects/8'](reply({ ...PROJECT, id: 8, name: 'Eight' })); });
    await act(async () => { pending['/api/v1/projects/7'](reply(PROJECT)); });
    expect(result.current.state.draft.name).toBe('Eight');
  });

  it('saves, stamping new rows with their ids, and makes the saved draft the clean baseline', async () => {
    const { api, result } = await loadedHook({ 'PUT /api/v1/projects/7/items/50': {}, 'POST /api/v1/projects/7/links': { id: 70 } });
    act(() => result.current.dispatch({ type: 'addLink' }));
    expect(result.current.isDirty).toBe(true);
    let pid = 0;
    await act(async () => { pid = await result.current.save(); });
    expect(pid).toBe(7);
    expect(result.current.state.draft.links[0].serverId).toBe(70);
    expect(result.current.isDirty).toBe(false);
    expect(api.to('POST', '/api/v1/projects/7/links')).toHaveLength(1);
  });

  it('keeps edits made while a save is running unsaved', async () => {
    let finish!: () => void;
    const gate = new Promise<void>(r => { finish = r; });
    const { result } = await loadedHook({ 'PUT /api/v1/projects/7/items/50': {} });
    act(() => result.current.dispatch({ type: 'setFields', fields: { notes: 'a' } }));
    type Fetch = (url: string, init?: RequestInit) => Promise<unknown>;
    const fetchMock = vi.mocked(globalThis.fetch as unknown as Fetch);
    const real = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) => { await gate; return real(url, init); });
    let saving!: Promise<number>;
    act(() => { saving = result.current.save(); });
    act(() => result.current.dispatch({ type: 'setFields', fields: { notes: 'ab' } }));
    await act(async () => { finish(); await saving; });
    expect(result.current.isDirty).toBe(true);
    act(() => result.current.dispatch({ type: 'setFields', fields: { notes: 'a' } }));
    expect(result.current.isDirty).toBe(false);
  });

  it('leaves the baseline alone when a save fails', async () => {
    const { result } = await loadedHook({ 'PATCH /api/v1/projects/7': new Reply(500, 'down') });
    act(() => result.current.dispatch({ type: 'setFields', fields: { notes: 'x' } }));
    await act(async () => { await expect(result.current.save()).rejects.toThrow(); });
    expect(result.current.isDirty).toBe(true);
  });

  it('drops the undo after UNDO_TIMEOUT_MS', async () => {
    const { result } = await loadedHook();
    const timeouts = vi.spyOn(window, 'setTimeout');
    act(() => result.current.dispatch({ type: 'remove', kind: 'item', localId: result.current.state.draft.items[0].localId }));
    expect(result.current.state.lastRemoved).not.toBeNull();
    const call = timeouts.mock.calls.find(c => c[1] === UNDO_TIMEOUT_MS)!;
    expect(UNDO_TIMEOUT_MS).toBe(6000);
    act(() => (call[0] as () => void)());
    expect(result.current.state.lastRemoved).toBeNull();
    expect(result.current.state.draft.items).toEqual([]);   // the removal itself stays
  });
});
