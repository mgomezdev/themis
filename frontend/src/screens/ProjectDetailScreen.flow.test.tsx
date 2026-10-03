import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectDetailScreen } from './ProjectDetailScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const PRINTERS = [
  { id: 1, name: 'Printer A', enabled: true, bed_x_mm: 256, bed_y_mm: 256 },
  { id: 2, name: 'Printer B', enabled: true, bed_x_mm: 256, bed_y_mm: 256 },
];
const ITEM = {
  id: 50, project_id: 42, file_id: 1, file_name: 'Bracket.stl', quantity: 4, quantity_completed: 0, quantity_failed: 0,
  filament_type: 'PLA', filament_color: '#ff0000', filament_id: null, sort_order: 0,
};
const project = (over: object = {}) => ({
  id: 42, name: 'Shelf set', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  result_file_id: null, source_app: null, source_user: null, source_layout_id: null, amount_paid: null,
  payment_status: 'unpaid', price_visible: false, quote_accepted_at: null, price: null, stage: 'planning', customer_id: null, created_at: '', updated_at: '',
  items: [ITEM], links: [], parts: [], jobs_total: 0, jobs_complete: 0,
  estimate_filament_grams_total: null, estimate_seconds_total: null, estimate_filament_grams_remaining: null,
  estimate_seconds_remaining: null, actual_filament_grams: null, actual_seconds: null, filament_cost_total: null,
  ...over,
});
const projectJob = (id: number, over: object = {}) => ({
  id, plate_number: 1, status: 'queued', queue_position: id, assigned_printer_id: null, block_reason: null, outcome: null,
  created_at: '', updated_at: '', completed_at: null, file_name: 'Bracket.stl', total_parts: 2, ...over,
});
const generated = (n: number) => ({ project_id: 42, jobs: Array.from({ length: n }, (_, i) => ({ id: i + 1 })), files: [], eligible_printer_ids: [], pack_bed_x: 256, pack_bed_y: 256 });

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }
const where = () => screen.getByTestId('where').textContent;

/** `state.project` is what GET /projects/42 returns next, so a promote/reload cycle can be observed. */
function open(over: Record<string, unknown> = {}, initial: object = project()) {
  const state = { project: initial as ReturnType<typeof project> };
  const api = stubFetch({
    'GET /api/v1/projects/42': () => state.project,
    'GET /api/v1/projects/42/jobs': [],
    'GET /api/v1/projects/42/payments': [],
    'GET /api/v1/printers': PRINTERS,
    'GET /api/v1/printers/1/profiles': { print_profiles: ['0.20mm Standard'], filament_profiles: [] },
    'GET /api/v1/printers/2/profiles': { print_profiles: ['0.20mm Standard'], filament_profiles: [] },
    ...over,
  });
  render(
    <MemoryRouter initialEntries={['/projects/42']}>
      <Where />
      <Routes>
        <Route path="/projects/:id" element={<ProjectDetailScreen />} />
        <Route path="/projects/:id/edit" element={<div>EDIT PAGE</div>} />
        <Route path="/jobs/:id" element={<div>JOB PAGE</div>} />
        <Route path="/queue" element={<div>QUEUE PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return { api, state };
}
const ready = () => screen.findByRole('heading', { name: 'Shelf set' });
const button = (name: string | RegExp) => screen.getByRole('button', { name });

afterEach(() => vi.unstubAllGlobals());

describe('ProjectDetailScreen - promoting a project', () => {
  it.each([
    ['draft', 'Promote to Planning', 'planning'],
    ['planning', 'Promote to Queued', 'queued'],
  ])('a %s project offers exactly one forward step and posts it', async (stage, label, next) => {
    const { api } = open({ 'POST /api/v1/projects/42/promote': {} }, project({ stage }));
    await ready();

    await userEvent.click(button(label));

    await waitFor(() => expect(api.to('POST', '/api/v1/projects/42/promote').map(c => c.body)).toEqual([{ stage: next }]));
    expect(screen.getAllByRole('button', { name: /^Promote to/ })).toHaveLength(1);
  });

  it('reloads after promoting so the badge and the next step move forward', async () => {
    const { api, state } = open({
      'POST /api/v1/projects/42/promote': () => { state.project = project({ stage: 'planning' }); return {}; },
    }, project({ stage: 'draft' }));
    await ready();
    expect(screen.getByText('Draft')).toBeTruthy();

    await userEvent.click(button('Promote to Planning'));

    expect(await screen.findByRole('button', { name: 'Promote to Queued' })).toBeTruthy();
    expect(screen.getByText('Planning')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Promote to Planning' })).toBeNull();
    expect(api.to('GET', '/api/v1/projects/42')).toHaveLength(2);
    expect(api.to('GET', '/api/v1/projects/42/jobs')).toHaveLength(2);
  });

  it('a queued project cannot be promoted any further', async () => {
    open({}, project({ stage: 'queued' }));
    await ready();

    expect(screen.queryByRole('button', { name: /^Promote to/ })).toBeNull();
    expect(screen.getByText('Queued')).toBeTruthy();
  });

  it('shows why a promotion was refused and leaves the stage alone', async () => {
    open({ 'POST /api/v1/projects/42/promote': new Reply(409, { detail: 'Not a forward transition' }) }, project({ stage: 'draft' }));
    await ready();

    await userEvent.click(button('Promote to Planning'));

    expect(await screen.findByText('Not a forward transition')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Promote to Planning' })).toBeTruthy();
  });
});

describe('ProjectDetailScreen - generating jobs', () => {
  it('cannot generate from a draft (promote first), or from a project with no parts', async () => {
    open({}, project({ stage: 'draft' }));
    await ready();

    const generate = button('Generate…');
    expect(generate.hasAttribute('disabled')).toBe(true);
    expect(generate.getAttribute('title')).toBe('Promote to planning before creating jobs');
  });

  it('cannot generate when the project has no parts', async () => {
    open({}, project({ items: [] }));
    await ready();

    expect(button('Generate…').hasAttribute('disabled')).toBe(true);
    expect(screen.getByText('No parts — edit the project to add parts.')).toBeTruthy();
  });

  it('generates for the chosen printers, reports the job count and reloads the job list', async () => {
    const { api, state } = open({
      'POST /api/v1/projects/42/generate': () => {
        state.project = project({ jobs_total: 2 });
        return generated(2);
      },
    });
    await ready();

    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('checkbox', { name: /Printer B/ }));
    await waitFor(() => expect(screen.getByTestId('process-preset-select').textContent).toContain('0.20mm Standard'));
    await userEvent.selectOptions(screen.getByTestId('process-preset-select'), '0.20mm Standard');
    await userEvent.click(button('Generate'));

    expect(await screen.findByText('2 jobs queued')).toBeTruthy();
    expect(api.to('POST', '/api/v1/projects/42/generate').map(c => c.body)).toEqual([
      { eligible_printer_ids: [2], process_preset: '0.20mm Standard', allow_cached: true, save_slice: false },
    ]);
    expect(screen.queryByText('Eligible printers')).toBeNull();          // picker closed
    await waitFor(() => expect(api.to('GET', '/api/v1/projects/42/jobs')).toHaveLength(2));   // refreshed after generating
  });

  it('generates without dispatch when no printer is ticked, and says "1 job" in the singular', async () => {
    const { api } = open({ 'POST /api/v1/projects/42/generate': generated(1) });
    await ready();

    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    expect(await screen.findByText('1 job queued')).toBeTruthy();
    expect(api.to('POST', '/api/v1/projects/42/generate').map(c => c.body)).toEqual([
      { eligible_printer_ids: [], process_preset: null, allow_cached: true, save_slice: false },
    ]);
  });

  it('sends the cache choices and reports a reused pack and cached plates', async () => {
    const { api } = open({ 'POST /api/v1/projects/42/generate': {
      ...generated(2),
      files: [{ id: 9, original_filename: 'project-x-PLA.3mf', folder: '/', plate_count: 2, pack_reused: true, cached_plates: 2 }],
    } });
    await ready();

    await userEvent.click(button('Generate…'));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use cached slices when available' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Save sliced gcode to library' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    expect(await screen.findByText('2 jobs queued')).toBeTruthy();
    expect(api.to('POST', '/api/v1/projects/42/generate').map(c => c.body)).toEqual([
      { eligible_printer_ids: [], process_preset: null, allow_cached: false, save_slice: true },
    ]);
    expect(screen.getByTestId('generate-cache-info').textContent).toBe(
      'project-x-PLA.3mf: reused previous pack, 2 plates have cached versions');
  });

  it('View Queue jumps to the queue', async () => {
    open({ 'POST /api/v1/projects/42/generate': generated(1) });
    await ready();
    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    await userEvent.click(await screen.findByRole('button', { name: 'View Queue' }));

    expect(where()).toBe('/queue');
  });

  it('shows the failure and keeps the project as it was', async () => {
    open({ 'POST /api/v1/projects/42/generate': new Reply(502, 'sidecar down') });
    await ready();
    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    expect(await screen.findByText('502 sidecar down')).toBeTruthy();
    expect(screen.queryByText(/jobs? queued/)).toBeNull();
    expect(button('Generate…').hasAttribute('disabled')).toBe(false);     // free to try again
  });

  it('reopening Generate… clears the previous error', async () => {
    open({ 'POST /api/v1/projects/42/generate': new Reply(502, 'sidecar down') });
    await ready();
    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('502 sidecar down');

    await userEvent.click(button('Generate…'));

    expect(screen.queryByText('502 sidecar down')).toBeNull();
  });

  it('Cancel closes the picker, and reopening Generate… clears the last result', async () => {
    const { api } = open({ 'POST /api/v1/projects/42/generate': generated(1) });
    await ready();
    await userEvent.click(button('Generate…'));
    await screen.findByText('Eligible printers');
    await userEvent.click(button('Cancel'));
    expect(screen.queryByText('Eligible printers')).toBeNull();
    expect(api.to('POST', '/api/v1/projects/42/generate')).toEqual([]);

    await userEvent.click(button('Generate…'));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('1 job queued');
    await userEvent.click(button('Generate…'));

    expect(screen.queryByText('1 job queued')).toBeNull();
  });
});

describe('ProjectDetailScreen - quote visibility', () => {
  const toggle = () => screen.getByRole('checkbox', { name: 'Show price to customer' }) as HTMLInputElement;

  it('lets staff show the price to the customer and hide it again', async () => {
    const { api } = open({
      'PATCH /api/v1/projects/42': (c: { body: { price_visible: boolean } }) =>
        project({ customer_id: 3, customer_name: 'Vela', price: 100, price_visible: c.body.price_visible }),
    }, project({ customer_id: 3, customer_name: 'Vela', price: 100, price_visible: false }));
    await ready();
    expect(toggle().checked).toBe(false);

    await userEvent.click(toggle());
    await waitFor(() => expect(toggle().checked).toBe(true));
    await userEvent.click(toggle());
    await waitFor(() => expect(toggle().checked).toBe(false));

    expect(api.to('PATCH', '/api/v1/projects/42').map(c => c.body)).toEqual([{ price_visible: true }, { price_visible: false }]);
  });

  it('is disabled until there is a price, and absent for a project with no customer account', async () => {
    open({}, project({ customer_id: 3, customer_name: 'Vela', price: null }));
    await ready();
    expect(toggle().disabled).toBe(true);
  });

  it('is not offered for a project without a customer account', async () => {
    open({}, project({ customer_id: null, price: 100 }));
    await ready();
    expect(screen.queryByRole('checkbox', { name: 'Show price to customer' })).toBeNull();
  });

  it('shows when the customer accepted the quote', async () => {
    open({}, project({ customer_id: 3, customer_name: 'Vela', price: 100, price_visible: true, quote_accepted_at: '2026-09-20T10:00:00Z' }));
    await ready();
    expect(screen.getByText(/Quote accepted/)).toBeTruthy();
  });
});

describe('ProjectDetailScreen - customer account', () => {
  const CUSTOMERS = [
    { id: 3, name: 'Vela Robotics', email: 'ops@vela.test', enabled: true, created_at: '' },
    { id: 4, name: 'Ada', email: 'ada@x.test', enabled: true, created_at: '' },
  ];
  const account = () => screen.getByLabelText('Account') as HTMLSelectElement;

  it('does not load customers until the account picker is focused, and only once', async () => {
    const { api } = open({ 'GET /api/v1/customers': CUSTOMERS });
    await ready();
    expect(api.to('GET', '/api/v1/customers')).toEqual([]);

    fireEvent.focus(account());
    await screen.findByRole('option', { name: 'Vela Robotics (ops@vela.test)' });
    fireEvent.focus(account());

    expect(api.to('GET', '/api/v1/customers')).toHaveLength(1);
  });

  it('links the project to the chosen customer and can unlink it again', async () => {
    const { api } = open({
      'GET /api/v1/customers': CUSTOMERS,
      'PATCH /api/v1/projects/42': (c: { body: { customer_id: number | null } }) => project({ customer_id: c.body.customer_id }),
    });
    await ready();
    fireEvent.focus(account());
    await screen.findByRole('option', { name: 'Ada (ada@x.test)' });

    await userEvent.selectOptions(account(), '4');
    await waitFor(() => expect(account().value).toBe('4'));
    await userEvent.selectOptions(account(), '');

    await waitFor(() => expect(api.to('PATCH', '/api/v1/projects/42').map(c => c.body)).toEqual([
      { customer_id: 4 }, { customer_id: null },
    ]));
    await waitFor(() => expect(account().value).toBe(''));
  });

  it('still shows an account that is not in the loaded list (e.g. no customers:read)', async () => {
    open({ 'GET /api/v1/customers': new Reply(403, 'forbidden') }, project({ customer_id: 9 }));
    await ready();

    expect(within(account()).getByRole('option', { name: 'Customer #9' })).toBeTruthy();
    fireEvent.focus(account());
    await waitFor(() => expect(account().value).toBe('9'));                  // a failed customer load doesn't break the page
    expect(within(account()).getByRole('option', { name: 'Customer #9' })).toBeTruthy();
  });

  it('does not keep retrying the customer list after it failed to load', async () => {
    const { api } = open({ 'GET /api/v1/customers': new Reply(403, 'forbidden') });
    await ready();

    fireEvent.focus(account());
    await waitFor(() => expect(api.to('GET', '/api/v1/customers')).toHaveLength(1));
    await new Promise(r => setTimeout(r, 20));
    fireEvent.focus(account());

    expect(api.to('GET', '/api/v1/customers')).toHaveLength(1);
  });

  it('shows why linking failed', async () => {
    open({ 'GET /api/v1/customers': CUSTOMERS, 'PATCH /api/v1/projects/42': new Reply(404, 'Customer not found') });
    await ready();
    fireEvent.focus(account());
    await screen.findByRole('option', { name: 'Ada (ada@x.test)' });

    await userEvent.selectOptions(account(), '4');

    expect(await screen.findByText('404 Customer not found')).toBeTruthy();
  });
});

describe('ProjectDetailScreen - non-printed parts', () => {
  const HARDWARE = [
    { id: 60, project_id: 42, name: '3mm magnet', quantity: 8, allocated: false, sort_order: 0, created_at: '' },
    { id: 61, project_id: 42, name: 'M3 screw', quantity: 4, allocated: true, sort_order: 1, created_at: '' },
  ];
  const tick = (name: string) => within(screen.getByText(name).closest('div[style*="grid"]') as HTMLElement).getByRole('checkbox') as HTMLInputElement;

  it('ticks a part as allocated straight away and saves it', async () => {
    const { api } = open({ 'PUT /api/v1/projects/42/parts/60': {} }, project({ parts: HARDWARE }));
    await ready();
    expect(tick('3mm magnet').checked).toBe(false);
    expect(tick('M3 screw').checked).toBe(true);

    await userEvent.click(tick('3mm magnet'));

    expect(tick('3mm magnet').checked).toBe(true);
    expect(api.to('PUT', '/api/v1/projects/42/parts/60').map(c => c.body)).toEqual([{ allocated: true }]);
    expect(screen.getAllByText('Yes')).toHaveLength(2);
  });

  it('un-allocating a part saves allocated: false', async () => {
    const { api } = open({ 'PUT /api/v1/projects/42/parts/61': {} }, project({ parts: HARDWARE }));
    await ready();

    await userEvent.click(tick('M3 screw'));

    expect(tick('M3 screw').checked).toBe(false);
    expect(api.to('PUT', '/api/v1/projects/42/parts/61').map(c => c.body)).toEqual([{ allocated: false }]);
    expect(screen.getAllByText('No')).toHaveLength(2);
  });

  it('puts the checkbox back (reloading from the server) when saving fails', async () => {
    const { api } = open({ 'PUT /api/v1/projects/42/parts/60': new Reply(500, 'nope') }, project({ parts: HARDWARE }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await ready();

    await userEvent.click(tick('3mm magnet'));

    await waitFor(() => expect(api.to('GET', '/api/v1/projects/42')).toHaveLength(2));
    await waitFor(() => expect(tick('3mm magnet').checked).toBe(false));
  });
});

describe('ProjectDetailScreen - parts, jobs and estimates', () => {
  it('lists parts with their filament requirement and print progress', async () => {
    open({}, project({
      items: [
        ITEM,
        { ...ITEM, id: 51, file_name: 'Widget.stl', quantity: 5, quantity_completed: 3, quantity_failed: 1, filament_type: 'any', filament_color: 'any', filament_id: 12 },
        { ...ITEM, id: 52, file_name: 'Done.stl', quantity: 2, quantity_completed: 2, filament_type: 'PETG', filament_color: 'any' },
      ],
    }));
    await ready();

    expect(screen.getByText('Parts (3)')).toBeTruthy();
    expect(screen.getByText('PLA / #ff0000')).toBeTruthy();
    expect(screen.getByText('PETG / any')).toBeTruthy();
    expect(screen.getByText('Spoolman #12')).toBeTruthy();                // a specific spool wins over type/colour
    expect(screen.getByText('3/5 · 1 failed')).toBeTruthy();
    expect(screen.getByText('2/2')).toBeTruthy();
    expect(screen.getByText('—')).toBeTruthy();                            // Bracket: nothing printed yet
  });

  it('shows each job with a readable status and opens its details', async () => {
    open({
      'GET /api/v1/projects/42/jobs': [
        projectJob(11, { status: 'complete' }),
        projectJob(12, { status: 'printing', plate_number: 2, total_parts: 0 }),
        projectJob(13, { status: 'mystery', file_name: null }),
      ],
    });
    await ready();

    expect(await screen.findByText('Jobs (3)')).toBeTruthy();
    expect(screen.getByText('Done')).toBeTruthy();                        // "complete" reads as Done
    expect(screen.getByText('Printing')).toBeTruthy();
    expect(screen.getByText('mystery')).toBeTruthy();                     // unknown status shown as-is
    expect(screen.getByText('p2')).toBeTruthy();

    await userEvent.click(screen.getAllByRole('button', { name: 'Details' })[1]);
    expect(where()).toBe('/jobs/12');
  });

  it('says how to create jobs when there are none yet', async () => {
    open();
    await ready();

    expect(await screen.findByText('No jobs yet — click Generate to create print jobs.')).toBeTruthy();
  });

  it('shows progress and estimate/actual totals once jobs exist', async () => {
    open({}, project({
      jobs_total: 4, jobs_complete: 1,
      estimate_filament_grams_total: 120.5, estimate_seconds_total: 7500,
      estimate_filament_grams_remaining: 90, estimate_seconds_remaining: 3600,
      actual_filament_grams: null, actual_seconds: 1800,
    }));
    await ready();

    expect(screen.getByText('1 / 4 jobs complete')).toBeTruthy();
    expect(screen.getByText(/Est\. total:\s*120\.5 g \/ 2h 5m/)).toBeTruthy();
    expect(screen.getByText(/Est\. remaining:\s*90\.0 g \/ 1h 0m/)).toBeTruthy();
    expect(screen.getByText(/Actual:\s*— \/ 30m/)).toBeTruthy();
  });

  it('shows a dash for an estimate the server has not produced yet', async () => {
    open({}, project({
      jobs_total: 2, jobs_complete: 0, estimate_seconds_total: 7500, estimate_filament_grams_total: 100,
      estimate_seconds_remaining: null, estimate_filament_grams_remaining: null,
    }));
    await ready();

    expect(screen.getByText(/Est\. total:\s*100\.0 g \/ 2h 5m/)).toBeTruthy();
    expect(screen.getByText(/Est\. remaining:\s*— \/ —/)).toBeTruthy();
  });

  it('hides the progress block until there are jobs', async () => {
    open();
    await ready();

    expect(screen.queryByText(/jobs complete/)).toBeNull();
  });

  it('shows customer, hold, payment and due-date details in the header', async () => {
    open({}, project({
      order_type: 'customer', customer: 'Vela Robotics', on_hold: true, payment_status: 'partial', amount_paid: 12.5,
      notes: 'rush', due_date: '2099-01-15',
    }));
    await ready();

    expect(screen.getByText('Vela Robotics')).toBeTruthy();
    expect(screen.getByText('Customer')).toBeTruthy();
    expect(screen.getByText('On hold')).toBeTruthy();
    expect(screen.getByText('partial · $12.50')).toBeTruthy();
    expect(screen.getByText('rush')).toBeTruthy();
    expect(screen.getByText(/^Due /).textContent).toContain('2099');
  });

  it('Edit opens the builder for this project', async () => {
    open();
    await ready();

    await userEvent.click(button('Edit'));

    expect(where()).toBe('/projects/42/edit');
  });
});

describe('ProjectDetailScreen - share link lifecycle', () => {
  const ENABLED = { enabled: true, token: 'tok-1', created_at: '2026-09-06T12:00:00Z' };
  const SHARE = '/api/v1/projects/42/share';

  it('regenerating asks first, then replaces the link', async () => {
    let current: object = ENABLED;
    const { api } = open({
      [`GET ${SHARE}`]: () => current,
      [`PUT ${SHARE}`]: () => (current = { ...ENABLED, token: 'tok-2' }),
    });
    await ready();
    await userEvent.click(button('Share'));
    await screen.findByDisplayValue(/tok-1/);

    await userEvent.click(button('Regenerate'));
    expect(screen.getByText('This invalidates the current link. Continue?')).toBeTruthy();
    await userEvent.click(button('Cancel'));
    expect(api.to('PUT', SHARE)).toEqual([]);                              // backing out changes nothing

    await userEvent.click(button('Regenerate'));
    await userEvent.click(button('Confirm'));

    expect(await screen.findByDisplayValue(/tok-2/)).toBeTruthy();
    expect(api.to('PUT', SHARE)).toHaveLength(1);
    expect(screen.queryByText(/Continue\?/)).toBeNull();
  });

  it('revoking asks first, then leaves the project unshared', async () => {
    const { api } = open({
      [`GET ${SHARE}`]: ENABLED,
      [`DELETE ${SHARE}`]: { enabled: false, token: null, created_at: null },
    });
    await ready();
    await userEvent.click(button('Share'));
    await screen.findByDisplayValue(/tok-1/);

    await userEvent.click(button('Revoke'));
    expect(screen.getByText('This disables the current link. Continue?')).toBeTruthy();
    await userEvent.click(button('Confirm'));

    expect(await screen.findByRole('button', { name: /create share link/i })).toBeTruthy();
    expect(api.to('DELETE', SHARE)).toHaveLength(1);
    expect(screen.queryByDisplayValue(/tok-1/)).toBeNull();
  });

  it('Copy puts the public /share URL on the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    open({ [`GET ${SHARE}`]: ENABLED });
    await ready();
    await userEvent.click(button('Share'));
    await screen.findByDisplayValue(/tok-1/);

    fireEvent.click(button('Copy'));

    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/share/tok-1`);
  });

  it('Share toggles the panel closed again', async () => {
    const { api } = open({ [`GET ${SHARE}`]: ENABLED });
    await ready();

    await userEvent.click(button('Share'));
    await screen.findByDisplayValue(/tok-1/);
    await userEvent.click(button('Share'));

    expect(screen.queryByDisplayValue(/tok-1/)).toBeNull();
    expect(api.to('GET', SHARE)).toHaveLength(1);                            // closing doesn't refetch
  });
});
