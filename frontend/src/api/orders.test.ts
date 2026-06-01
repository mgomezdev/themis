import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getOrders, getOrder, createOrder, updateOrder, deleteOrder } from './orders';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function mockOk(body: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

beforeEach(() => vi.clearAllMocks());

describe('orders api', () => {
  it('getOrders fetches the list', async () => {
    mockOk([]);
    const r = await getOrders();
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders');
    expect(Array.isArray(r)).toBe(true);
  });

  it('getOrder fetches one', async () => {
    mockOk({ id: 7, jobs: [] });
    const r = await getOrder(7);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/7');
    expect(r.id).toBe(7);
  });

  it('createOrder POSTs', async () => {
    mockOk({ id: 1 });
    await createOrder({ order_type: 'customer', customer: 'A', title: 'T', due_date: null, notes: null, parts: [] });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders', expect.objectContaining({ method: 'POST' }));
  });

  it('updateOrder PATCHes', async () => {
    mockOk({ id: 1, on_hold: true });
    await updateOrder(1, { on_hold: true });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/1', expect.objectContaining({ method: 'PATCH' }));
  });

  it('deleteOrder DELETEs', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, text: () => Promise.resolve(''), json: () => Promise.resolve(null) });
    await deleteOrder(1);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/1', expect.objectContaining({ method: 'DELETE' }));
  });
});
