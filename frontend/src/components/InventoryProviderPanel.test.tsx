import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InventoryProviderPanel } from './InventoryProviderPanel';
import { Reply, stubFetch } from '../test/fetchStub';
import { ALL_CAPS, mkStatus } from '../test/inventoryFixtures';
import type { PendingWrite } from '../api/inventory';

const write = (id: number, over: Partial<PendingWrite> = {}): PendingWrite => ({
  id, provider: 'p', spool_ref: String(id + 10), target_g: 412.4, job_id: 5, printer_id: 1, source: 'queue',
  created_at: '2026-01-01T00:00:00Z', attempts: 2, last_attempt_at: null, last_error: 'connection refused', status: 'pending', ...over,
});

const routes = (over: Record<string, unknown> = {}) => ({
  'GET /api/v1/inventory/sync-status': mkStatus({ provider: 'p' }),
  'GET /api/v1/inventory/pending-writes': { provider: 'p', items: [] },
  'GET /api/v1/inventory/tracking': { provider: 'p', items: [] },
  ...over,
});
const caps = (c: string[]) => ({ capabilities: c });

describe('InventoryProviderPanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('a provider that is not remote shows no sync or queue sections and makes no sync requests', async () => {
    const api = stubFetch(routes({ 'GET /api/v1/inventory/tracking': { provider: 'p', items: [{ spool_ref: '3', reason: 'No starting weight', since: '2026-01-01T00:00:00Z', job_id: 9 }] } }));
    render(<InventoryProviderPanel plugin={caps(['TRACKS_WEIGHT'])} />);

    expect(await screen.findByTestId('suspended')).toBeTruthy();                       // tracking still applies
    expect(screen.queryByText('Sync status')).toBeNull();
    expect(screen.queryByTestId('pending-writes')).toBeNull();
    expect(api.to('GET', '/api/v1/inventory/sync-status')).toEqual([]);
    expect(api.to('GET', '/api/v1/inventory/pending-writes')).toEqual([]);
  });

  it('shows sync health, cache age, an open outage and the last error for a remote provider', async () => {
    stubFetch(routes({ 'GET /api/v1/inventory/sync-status': mkStatus({
      disconnected_since: '2026-01-01T10:00:00Z', max_disconnect_minutes: 30, disconnect_alerted: true,
      last_error: 'refused', last_error_code: 'ConnectError', cache_as_of: '2026-01-01T09:00:00Z' }) }));
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);

    expect((await screen.findByTestId('sync-tone')).textContent).toBe('Unreachable');
    expect(screen.getByTestId('outage').textContent).toMatch(/alert after 30 min — sent/);
    expect(screen.getByText(/Last-known data cached/)).toBeTruthy();
    expect(screen.getByText('[ConnectError] refused')).toBeTruthy();
  });

  it('lists queued weight updates and applies or discards one', async () => {
    const api = stubFetch(routes({
      'GET /api/v1/inventory/pending-writes': { provider: 'p', items: [write(1), write(2, { source: 'manual_complete', job_id: null, attempts: 1 })] },
      'POST /api/v1/inventory/pending-writes/1/resolve': write(1, { status: 'applied' }),
      'POST /api/v1/inventory/pending-writes/2/discard': write(2, { status: 'discarded' }),
    }));
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);

    const first = within(await screen.findByTestId('pending-1'));
    expect(first.getByText(/Spool #11 →/)).toBeTruthy();
    expect(first.getByText('412 g')).toBeTruthy();
    expect(first.getByText(/job #5 · 2 attempts/)).toBeTruthy();
    expect(first.getByText('connection refused')).toBeTruthy();
    expect(within(screen.getByTestId('pending-2')).getByText(/manual completion · 1 attempt$/)).toBeTruthy();

    await userEvent.click(first.getByRole('button', { name: 'Apply now' }));
    await userEvent.click(screen.getByRole('button', { name: 'Discard update for spool 12' }));

    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/pending-writes/1/resolve')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/inventory/pending-writes/2/discard')).toHaveLength(1);
  });

  it('survives a malformed list response instead of crashing the page', async () => {
    stubFetch(routes({ 'GET /api/v1/inventory/pending-writes': {}, 'GET /api/v1/inventory/tracking': {} }));
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);
    expect(await screen.findByText(/every deduction has reached the provider/)).toBeTruthy();
  });

  it('says so when nothing is queued', async () => {
    stubFetch(routes());
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);
    expect(await screen.findByText(/every deduction has reached the provider/)).toBeTruthy();
  });

  it('resumes a suspended spool: confirming the weight, or writing a corrected one', async () => {
    const tracking = { provider: 'p', items: [
      { spool_ref: '3', reason: 'No starting weight was available for this spool', since: '2026-01-01T00:00:00Z', job_id: 9 },
      { spool_ref: '4', reason: 'No starting weight was available for this spool', since: '2026-01-01T00:00:00Z', job_id: 10 }] };
    const api = stubFetch(routes({
      'GET /api/v1/inventory/tracking': tracking,
      'POST /api/v1/inventory/spools/3/resume-tracking': { tracking: 'ok' },
      'POST /api/v1/inventory/spools/4/resume-tracking': { tracking: 'ok' },
    }));
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);

    const three = within(await screen.findByTestId('suspended-3'));
    await userEvent.click(three.getByRole('button', { name: 'Weight is correct — resume' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/spools/3/resume-tracking')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/inventory/spools/3/resume-tracking')[0].body).toEqual({});

    const four = within(screen.getByTestId('suspended-4'));
    await userEvent.type(four.getByLabelText('Corrected weight for spool 4'), '321');
    await userEvent.click(four.getByRole('button', { name: 'Set weight & resume' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/spools/4/resume-tracking')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/inventory/spools/4/resume-tracking')[0].body).toEqual({ remaining_g: 321 });
  });

  it('shows the error when an action fails', async () => {
    stubFetch(routes({ 'POST /api/v1/inventory/sync-now': new Reply(503, { detail: 'provider unreachable' }) }));
    render(<InventoryProviderPanel plugin={caps(ALL_CAPS)} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sync now' }));
    expect((await screen.findByRole('alert')).textContent).toBe('provider unreachable');
  });
});
