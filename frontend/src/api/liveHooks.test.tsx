import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useQueue } from './queue';
import { useFleetData } from './fleet';
import { useOrders } from './orders';
import { Reply, stubFetch } from '../test/fetchStub';

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeWS.instances.push(this); }
  close() { this.closed = true; }
  send(msg: unknown) { this.onmessage?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) } as MessageEvent); }
}
const sock = () => FakeWS.instances[FakeWS.instances.length - 1];

beforeEach(() => { FakeWS.instances = []; vi.stubGlobal('WebSocket', FakeWS); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const job = (id: number, status = 'queued', extra: object = {}) => ({ id, status, queue_position: id, materials: ['PLA'], ...extra });

/** Spy on setTimeout (passing through, so waitFor keeps working) to find the reconnect the socket schedules.
 *  Faking timers instead would hang Testing Library's async helpers. */
function watchTimers() { return vi.spyOn(globalThis, 'setTimeout'); }

/** Drop the socket, run the 1 s back-off timer it scheduled, and open the replacement, as a restarted server would allow. */
async function reconnect(timers: ReturnType<typeof watchTimers>) {
  const before = FakeWS.instances.length;
  await act(async () => {
    sock().onclose!();
    const retry = timers.mock.calls.filter(([, ms]) => ms === 1000).pop();
    expect(retry).toBeTruthy();
    (retry![0] as () => void)();
  });
  expect(FakeWS.instances).toHaveLength(before + 1);
  await act(async () => { sock().onopen!(); });
}

describe('useQueue', () => {
  it('starts empty, loads the queue, and reloads on refetch()', async () => {
    let queue = [job(1)];
    const api = stubFetch({ 'GET /api/v1/queue': () => queue });
    const { result } = renderHook(() => useQueue());

    await waitFor(() => expect(result.current.jobs.map(j => j.id)).toEqual([1]));
    queue = [job(1), job(2)];
    act(() => result.current.refetch());

    await waitFor(() => expect(result.current.jobs.map(j => j.id)).toEqual([1, 2]));
    expect(api.to('GET', '/api/v1/queue')).toHaveLength(2);
  });

  it('stays empty (and logs) when the first load fails, then recovers on the next refetch', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    let fail = true;
    stubFetch({ 'GET /api/v1/queue': () => (fail ? new Reply(500, 'db locked') : [job(1)]) });
    const { result } = renderHook(() => useQueue());
    await waitFor(() => expect(log).toHaveBeenCalled());
    expect(result.current.jobs).toEqual([]);

    fail = false;
    act(() => result.current.refetch());

    await waitFor(() => expect(result.current.jobs.map(j => j.id)).toEqual([1]));
  });

  it('a queue_update replaces the list but keeps the details it already had for surviving jobs', async () => {
    stubFetch({ 'GET /api/v1/queue': [job(1, 'queued', { materials: ['PETG'] }), job(2)] });
    const { result } = renderHook(() => useQueue());
    await waitFor(() => expect(result.current.jobs).toHaveLength(2));

    act(() => sock().send({ type: 'queue_update', data: [{ id: 2, status: 'printing', queue_position: 1 }, { id: 3, status: 'queued', queue_position: 2 }] }));

    expect(result.current.jobs.map(j => [j.id, j.status])).toEqual([[2, 'printing'], [3, 'queued']]);   // job 1 gone, job 3 new
    expect(result.current.jobs[0].materials).toEqual(['PLA']);                                          // enriched field survived
  });

  it('a job_update patches a known job, adds an unknown one, and removes finished or cancelled ones', async () => {
    stubFetch({ 'GET /api/v1/queue': [job(1), job(2), job(3)] });
    const { result } = renderHook(() => useQueue());
    await waitFor(() => expect(result.current.jobs).toHaveLength(3));

    act(() => sock().send({ type: 'job_update', data: job(1, 'printing') }));
    expect(result.current.jobs.find(j => j.id === 1)).toMatchObject({ status: 'printing', materials: ['PLA'] });
    act(() => sock().send({ type: 'job_update', data: job(4) }));
    expect(result.current.jobs.map(j => j.id)).toEqual([1, 2, 3, 4]);
    act(() => sock().send({ type: 'job_update', data: job(2, 'complete') }));
    act(() => sock().send({ type: 'job_update', data: job(3, 'cancelled') }));

    expect(result.current.jobs.map(j => j.id)).toEqual([1, 4]);
  });

  it('ignores malformed frames and message types it does not handle', async () => {
    stubFetch({ 'GET /api/v1/queue': [job(1)] });
    const { result } = renderHook(() => useQueue());
    await waitFor(() => expect(result.current.jobs).toHaveLength(1));

    act(() => sock().send('not json'));
    act(() => sock().send({ type: 'printer_state', data: { id: 9 } }));
    act(() => sock().send({ type: 'queue_update', data: 'nope' }));

    expect(result.current.jobs.map(j => j.id)).toEqual([1]);
  });

  it('closes its socket on unmount', async () => {
    stubFetch({ 'GET /api/v1/queue': [] });
    const { unmount } = renderHook(() => useQueue());
    const socket = sock();

    unmount();

    expect(socket.closed).toBe(true);
  });

  it('after the connection drops and returns, reloads the queue to pick up what it missed', async () => {
    const timers = watchTimers();
    let queue = [job(1)];
    const api = stubFetch({ 'GET /api/v1/queue': () => queue });
    const { result } = renderHook(() => useQueue());
    await waitFor(() => expect(result.current.jobs.map(j => j.id)).toEqual([1]));
    queue = [job(1), job(2, 'printing')];                                   // happened while the socket was down

    await reconnect(timers);

    await waitFor(() => expect(result.current.jobs.map(j => j.id)).toEqual([1, 2]));
    expect(api.to('GET', '/api/v1/queue')).toHaveLength(2);
    expect(FakeWS.instances).toHaveLength(2);
    act(() => sock().send({ type: 'job_update', data: job(2, 'complete') }));   // and live updates flow on the new socket
    expect(result.current.jobs.map(j => j.id)).toEqual([1]);
  });
});

const fleetPrinter = (id: number, over: object = {}) => ({
  id, name: `P${id}`, printer_type: 'bambu', enabled: true, queue_on: true, connected: true, awaiting_plate_clear: false,
  no_snapshots_while_idle: false, loaded_filaments: [], state: 'IDLE', progress: 0, remaining_time: 0, layer_num: null,
  total_layers: null, temperatures: {}, capabilities: {}, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0, ...over,
});

describe('useFleetData', () => {
  it('loads printers, merges printer_state frames by id and adds unknown printers', async () => {
    stubFetch({ 'GET /api/v1/fleet': [fleetPrinter(1), fleetPrinter(2)] });
    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(result.current[0]).toHaveLength(2));

    act(() => sock().send({ type: 'printer_state', data: { id: 2, state: 'RUNNING', progress: 41.6 } }));
    act(() => sock().send({ type: 'printer_state', data: fleetPrinter(3) }));

    const printers = result.current[0];
    expect(printers.map(p => p.id)).toEqual(['1', '2', '3']);
    expect(printers[1]).toMatchObject({ status: 'printing', progress: 42, name: 'P2' });
    act(() => sock().send({ type: 'printer_state', data: { state: 'RUNNING' } }));            // no id: ignored
    act(() => sock().send('garbage'));
    expect(result.current[0]).toHaveLength(3);
  });

  it('reloads the fleet after a reconnect and closes its socket on unmount', async () => {
    const timers = watchTimers();
    let fleet = [fleetPrinter(1)];
    const api = stubFetch({ 'GET /api/v1/fleet': () => fleet });
    const { result, unmount } = renderHook(() => useFleetData());
    await waitFor(() => expect(result.current[0]).toHaveLength(1));
    fleet = [fleetPrinter(1, { state: 'RUNNING' })];

    await reconnect(timers);

    await waitFor(() => expect(result.current[0][0].status).toBe('printing'));
    expect(api.to('GET', '/api/v1/fleet')).toHaveLength(2);
    const live = sock();
    unmount();
    expect(live.closed).toBe(true);
  });
});

describe('useOrders', () => {
  const order = (id: number, progress: number) => ({ id, title: `O${id}`, progress });

  it('refetches when a job or queue update arrives, but not for other frames', async () => {
    let orders = [order(1, 0)];
    const api = stubFetch({ 'GET /api/v1/orders': () => orders });
    const { result } = renderHook(() => useOrders());
    await waitFor(() => expect(result.current.orders).toHaveLength(1));
    orders = [order(1, 0.5)];

    act(() => sock().send({ type: 'printer_state', data: {} }));
    act(() => sock().send('garbage'));
    expect(api.to('GET', '/api/v1/orders')).toHaveLength(1);
    act(() => sock().send({ type: 'job_update', data: {} }));

    await waitFor(() => expect((result.current.orders[0] as unknown as { progress: number }).progress).toBe(0.5));
    orders = [order(1, 1)];
    act(() => sock().send({ type: 'queue_update', data: [] }));
    await waitFor(() => expect((result.current.orders[0] as unknown as { progress: number }).progress).toBe(1));
  });

  it('reloads orders after a reconnect and stops listening after unmount', async () => {
    const timers = watchTimers();
    let orders = [order(1, 0)];
    const api = stubFetch({ 'GET /api/v1/orders': () => orders });
    const { result, unmount } = renderHook(() => useOrders());
    await waitFor(() => expect(result.current.orders).toHaveLength(1));
    orders = [order(1, 0), order(2, 0)];

    await reconnect(timers);

    await waitFor(() => expect(result.current.orders).toHaveLength(2));
    expect(api.to('GET', '/api/v1/orders')).toHaveLength(2);
    const live = sock();
    unmount();
    expect(live.closed).toBe(true);
  });
});
