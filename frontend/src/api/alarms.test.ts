import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { acknowledgeAlarm, acknowledgeAll, listAlarms, saveAlarmSettings, useAlarmSummary } from './alarms';
import { stubFetch } from '../test/fetchStub';

afterEach(() => vi.unstubAllGlobals());

class MockWS {
  static instances: MockWS[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  close = vi.fn();
  constructor() { MockWS.instances.push(this); }
}

describe('alarms api', () => {
  it('builds the list query from status and printer', async () => {
    const { calls } = stubFetch({
      'GET /api/v1/alarms?status=all&printer_id=4': [],
      'GET /api/v1/alarms?status=active': { not: 'an array' },
    });
    expect(await listAlarms('all', 4)).toEqual([]);
    expect(await listAlarms('active')).toEqual([]);                        // a malformed body is treated as no alarms
    expect(calls).toHaveLength(2);
  });

  it('acknowledges one, all, or one printer\'s, and saves the threshold', async () => {
    const { calls } = stubFetch({
      'POST /api/v1/alarms/5/acknowledge': { id: 5 },
      'POST /api/v1/alarms/acknowledge-all': { acknowledged: 2 },
      'POST /api/v1/alarms/acknowledge-all?printer_id=3': { acknowledged: 1 },
      'PUT /api/v1/alarms/settings': { min_severity: 'error' },
    });
    await acknowledgeAlarm(5);
    await acknowledgeAll();
    await acknowledgeAll(3);
    await saveAlarmSettings('error');
    expect(calls.map(c => `${c.method} ${c.url}`)).toEqual([
      'POST /api/v1/alarms/5/acknowledge', 'POST /api/v1/alarms/acknowledge-all',
      'POST /api/v1/alarms/acknowledge-all?printer_id=3', 'PUT /api/v1/alarms/settings']);
    expect(calls[3].body).toEqual({ min_severity: 'error' });
  });
});

describe('useAlarmSummary', () => {
  it('loads the summary and reloads when the server announces alarms_changed', async () => {
    MockWS.instances = [];
    vi.stubGlobal('WebSocket', MockWS);
    let count = 1;
    stubFetch({ 'GET /api/v1/alarms/summary': () => ({ count, worst: 'error', printers: [] }) });
    const { result } = renderHook(() => useAlarmSummary());
    await waitFor(() => expect(result.current.count).toBe(1));

    count = 4;
    act(() => { MockWS.instances[0].onmessage?.({ data: JSON.stringify({ type: 'printer_state', data: {} }) }); });
    await new Promise(r => setTimeout(r, 20));
    expect(result.current.count).toBe(1);                                  // an unrelated event does not reload

    act(() => { MockWS.instances[0].onmessage?.({ data: JSON.stringify({ type: 'alarms_changed', data: { printer_id: 1 } }) }); });
    await waitFor(() => expect(result.current.count).toBe(4));
  });

  it('falls back to an empty summary on a malformed response', async () => {
    vi.stubGlobal('WebSocket', MockWS);
    stubFetch({ 'GET /api/v1/alarms/summary': {} });
    const { result } = renderHook(() => useAlarmSummary());
    await new Promise(r => setTimeout(r, 20));
    expect(result.current).toEqual({ count: 0, worst: null, printers: [] });
  });
});
