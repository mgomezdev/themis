import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueueScreen } from './QueueScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const est = {
  actual_filament_grams: null, actual_seconds: null, actual_filament_breakdown: null, deduction_skipped: null,
  estimate_status: null, estimate_seconds: null, estimate_filament_grams: null, estimate_filament_breakdown: null,
  estimate_preset_label: null, materials: [], eligible_printers: [], model_targets: [], low_stock_warning: null, filament_cost: null,
};
const job = (id: number, plate: number, status: string, over: object = {}) => ({
  id, uploaded_file_id: 10, plate_number: plate, order_id: null, assigned_printer_id: null, queue_position: id,
  status, overrides: null, block_reason: null, created_at: '', updated_at: '', ...est, ...over,
});
const QUEUED = job(8, 1, 'queued');
const OTHER = job(9, 2, 'queued');
const BLOCKED = job(11, 3, 'blocked', { block_reason: 'Slicing failed: no compatible profile' });

class FakeWS { onmessage: unknown = null; close() {} }

function open(queue: object[] = [QUEUED, OTHER], over: Record<string, unknown> = {}) {
  vi.stubGlobal('WebSocket', FakeWS);
  const api = stubFetch({
    'GET /api/v1/queue': () => queue,
    'GET /api/v1/fleet': [],
    'GET /api/v1/laminus/catalog/status': { laminus_configured: true, laminus: {} },
    'GET /api/v1/files/10/plates': { filename: 'gear.3mf', plates: [1, 2, 3].map(n => ({ plate_number: n, estimated_time: 600, filament_g: 12, thumbnail_path: null })) },
    ...over,
  });
  render(<MemoryRouter><QueueScreen /></MemoryRouter>);
  return api;
}
/** Open the detail panel of the job card showing "Plate N". */
async function select(plate: number) {
  await userEvent.click(await screen.findByText(`Plate ${plate}`));
}
const alertText = () => screen.queryByRole('alert')?.textContent ?? null;

afterEach(() => vi.unstubAllGlobals());

describe('QueueScreen - cancelling a job', () => {
  it('cancels the selected job (not another), reloads the queue and closes the panel', async () => {
    let queue: object[] = [QUEUED, OTHER];
    const api = open(queue, {
      'GET /api/v1/queue': () => queue,
      'POST /api/v1/jobs/9/cancel': () => { queue = [QUEUED]; return { ...OTHER, status: 'cancelled' }; },
    });
    await select(2);

    await userEvent.click(screen.getByRole('button', { name: /remove from queue/i }));

    await waitFor(() => expect(screen.queryByText('Plate 2')).toBeNull());
    expect(api.calls.filter(c => c.url.endsWith('/cancel')).map(c => c.url)).toEqual(['/api/v1/jobs/9/cancel']);
    expect(screen.getByText('Plate 1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /remove from queue/i })).toBeNull();   // panel closed
    expect(api.to('GET', '/api/v1/queue')).toHaveLength(2);                             // reloaded
    expect(alertText()).toBeNull();
  });

  it('closes the panel on a successful cancel even if the reloaded queue still lists the job for a moment', async () => {
    open([QUEUED, OTHER], { 'POST /api/v1/jobs/9/cancel': { ...OTHER, status: 'cancelled' } });   // queue unchanged
    await select(2);

    await userEvent.click(screen.getByRole('button', { name: /remove from queue/i }));

    await waitFor(() => expect(screen.queryByRole('button', { name: /remove from queue/i })).toBeNull());
    expect(screen.getAllByText('Plate 2')).toHaveLength(1);              // only the card, no panel
  });

  it('tells the operator when the cancel is rejected and leaves the job and its panel alone', async () => {
    const api = open([QUEUED, OTHER], {
      'POST /api/v1/jobs/8/cancel': new Reply(409, { detail: 'Job is already complete' }),
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await select(1);

    await userEvent.click(screen.getByRole('button', { name: /remove from queue/i }));

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(alertText()).toContain('Failed to cancel job #8: 409 {"detail":"Job is already complete"}');
    expect(screen.getAllByText('Plate 1').length).toBeGreaterThan(0);                  // card (and panel) still there
    expect(screen.getByRole('button', { name: /remove from queue/i })).toBeTruthy();   // still open, can retry
    expect(api.to('GET', '/api/v1/queue')).toHaveLength(1);                             // nothing changed, no reload
  });

  it('clears the message when dismissed, and when the next action starts', async () => {
    let attempts = 0;
    open([QUEUED, OTHER], {
      'POST /api/v1/jobs/8/cancel': () => (++attempts < 3 ? new Reply(500, 'boom') : QUEUED),
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await select(1);
    const remove = () => screen.getByRole('button', { name: /remove from queue/i });

    await userEvent.click(remove());
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(alertText()).toBeNull();

    await userEvent.click(remove());
    await waitFor(() => expect(alertText()).toContain('500 boom'));
    await userEvent.click(remove());                                     // third attempt succeeds

    await waitFor(() => expect(alertText()).toBeNull());
  });
});

describe('QueueScreen - unblocking a job', () => {
  it('asks the server to retry the blocked job and reloads the queue', async () => {
    const api = open([QUEUED, BLOCKED], { 'POST /api/v1/jobs/11/unblock': { ...BLOCKED, status: 'queued' } });
    await select(3);

    await userEvent.click(screen.getByRole('button', { name: /unblock/i }));

    await waitFor(() => expect(api.to('GET', '/api/v1/queue')).toHaveLength(2));
    expect(api.calls.filter(c => c.url.endsWith('/unblock')).map(c => c.url)).toEqual(['/api/v1/jobs/11/unblock']);
    expect(alertText()).toBeNull();
  });

  it('tells the operator when the unblock is rejected', async () => {
    open([QUEUED, BLOCKED], { 'POST /api/v1/jobs/11/unblock': new Reply(409, { detail: 'Job is not blocked' }) });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await select(3);

    await userEvent.click(screen.getByRole('button', { name: /unblock/i }));

    await waitFor(() => expect(alertText()).toContain('Failed to unblock job #11: 409 {"detail":"Job is not blocked"}'));
    expect(screen.getByRole('button', { name: /unblock/i })).toBeTruthy();
  });
});

describe('QueueScreen - reordering', () => {
  it.each([
    [/front/i, 'front'],
    [/up/i, 'promote'],
    [/down/i, 'demote'],
    [/back/i, 'back'],
  ])('%s sends the "%s" action for the selected job and reloads', async (label, action) => {
    const api = open([QUEUED, OTHER], { 'POST /api/v1/jobs/9/reorder': OTHER });
    await select(2);

    await userEvent.click(screen.getByRole('button', { name: label }));

    await waitFor(() => expect(api.to('GET', '/api/v1/queue')).toHaveLength(2));
    expect(api.to('POST', '/api/v1/jobs/9/reorder').map(c => c.body)).toEqual([{ action }]);
  });

  it('tells the operator when the move is rejected', async () => {
    open([QUEUED, OTHER], { 'POST /api/v1/jobs/8/reorder': new Reply(409, { detail: 'Job is not queued' }) });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await select(1);

    await userEvent.click(screen.getByRole('button', { name: /front/i }));

    await waitFor(() => expect(alertText()).toContain('Failed to reorder job #8: 409 {"detail":"Job is not queued"}'));
  });
});

describe('QueueScreen - health banner', () => {
  it('warns that slicing is paused when the Laminus sidecar cannot be reached', async () => {
    open([QUEUED], { 'GET /api/v1/laminus/catalog/status': new Reply(502, 'bad gateway') });

    expect(await screen.findByText(/Laminus sidecar is unreachable/)).toBeTruthy();
    expect(within(screen.getByText(/Laminus sidecar is unreachable/).parentElement!).getByText(/Slicing is paused/)).toBeTruthy();
  });

  it('stays quiet while Laminus is up', async () => {
    open([QUEUED]);
    await screen.findByText('Plate 1');

    expect(screen.queryByText(/Laminus sidecar is unreachable/)).toBeNull();
  });
});
