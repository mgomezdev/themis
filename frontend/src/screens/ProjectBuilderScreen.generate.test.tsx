import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const FILES = [
  { id: 1, original_filename: 'Bracket.stl', relative_path: 'Bracket.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
  { id: 2, original_filename: 'Widget.stl', relative_path: 'Widget.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
];
const printer = (id: number, name: string, extra: object = {}) => ({
  id, name, printer_type: 'bambu_x1c', connection_config: {}, awaiting_plate_clear: false, orca_printer_profiles: [],
  machine_profile_override: null, enabled: true, queue_on: true, connected: true, loaded_filaments: [],
  build_plate_type: null, no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256, ...extra,
});
const PRINTERS = [printer(1, 'Printer A'), printer(2, 'Printer B')];
const group = (id: number, name: string, over: object = {}) => ({
  id, project_id: 7, name, sort_order: id, is_default: id === 1, strategy: 'fixed', envelope_x: null, envelope_y: null,
  mix_colours: false, targets: [], max_parts: null, pool: null, pool_warning: null, ...over,
});
const GROUPS = [group(1, 'Default Group'), group(2, 'Fine')];

const EXISTING = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  amount_paid: null, payment_status: 'unpaid', stage: 'planning', customer_id: null, links: [], parts: [],
  items: [{ id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 2, quantity_completed: 0, quantity_failed: 0,
            filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 0, group_id: 1 }],
};
const job = (id: number) => ({ id, uploaded_file_id: 1, plate_number: 1, queue_position: id, status: 'queued' });
const generated = (n: number, extra: object = {}) => ({ project_id: 7, jobs: Array.from({ length: n }, (_, i) => job(i + 1)), files: [],
                                                        eligible_printer_ids: [], pack_bed_x: 256, pack_bed_y: 256, ...extra });
const CLEAN = { checks: [{ level: 'ok', tab: 'items', message: 'Every item is in a group', group_id: null, item_id: null }], open: 0 };

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }

/** The builder routed as in App.tsx, so post-save navigation behaves like the real app. */
function open(path: string, over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/files': FILES,
    'GET /api/v1/plugins': { plugins: [], selections: {} },
    'GET /api/v1/printers': PRINTERS,
    'GET /api/v1/printers/1/profiles': { print_profiles: ['0.20mm Standard'], filament_profiles: [] },
    'GET /api/v1/printers/2/profiles': { print_profiles: ['0.20mm Standard'], filament_profiles: [] },
    'GET /api/v1/projects/7': EXISTING,
    'GET /api/v1/projects/7/groups': GROUPS,
    'GET /api/v1/projects/7/review': CLEAN,
    'POST /api/v1/projects': { ...EXISTING, id: 7, items: [] },
    'POST /api/v1/projects/7/items': { id: 51 },
    'POST /api/v1/projects/7/links': { id: 70 },
    'POST /api/v1/projects/7/parts': { id: 80 },
    'POST /api/v1/projects/7/groups': group(3, 'Group 2'),
    'PUT /api/v1/projects/7/groups/order': (c: { body: unknown }) =>
      (c.body as { group_ids: number[] }).group_ids.map(id => GROUPS.find(g => g.id === id) ?? group(id, `G${id}`)),
    'PATCH /api/v1/projects/7': EXISTING,
    'PUT /api/v1/projects/7/items/50': EXISTING.items[0],
    'PUT /api/v1/projects/7/items/51': { id: 51 },
    'DELETE /api/v1/projects/7/items/50': { deleted: 50 },
    'POST /api/v1/projects/7/generate': generated(2),
    ...over,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route path="/projects/new" element={<ProjectBuilderScreen />} />
        <Route path="/projects/:id" element={<div>DETAIL PAGE</div>} />
        <Route path="/projects/:id/edit" element={<ProjectBuilderScreen />} />
        <Route path="/projects" element={<div>PROJECTS PAGE</div>} />
        <Route path="/queue" element={<div>QUEUE PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}
const where = () => screen.getByTestId('where').textContent;
/** Method + url of every request the builder made to the project endpoints, in order. */
const writes = (api: ReturnType<typeof stubFetch>) =>
  api.calls.filter(c => c.method !== 'GET' && c.url.startsWith('/api/v1/projects')).map(c => `${c.method} ${c.url}`);
const toTab = (name: RegExp) => userEvent.click(screen.getByRole('tab', { name }));
const queueBtn = () => screen.getByRole('button', { name: /Queue project|Queueing…/ }) as HTMLButtonElement;
async function existing(api = open('/projects/7/edit')) {
  await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
  return api;
}
async function reviewAndQueue() {
  await toTab(/^Review/);
  await waitFor(() => expect(queueBtn().disabled).toBe(false));
  await userEvent.click(queueBtn());
}

afterEach(() => vi.unstubAllGlobals());

describe('ProjectBuilderScreen - Save draft', () => {
  it('saves a new project with every header field, part order, links and parts, and stays in the Builder', async () => {
    const api = open('/projects/new');
    await userEvent.type(screen.getByLabelText(/Project name/), 'Vela order');
    await userEvent.selectOptions(screen.getAllByRole('combobox').find(el => within(el as HTMLElement).queryByRole('option', { name: 'Customer' }))!, 'customer');
    await userEvent.type(screen.getByPlaceholderText('Customer name'), 'Vela Robotics');
    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'rush');
    await userEvent.click(screen.getByRole('checkbox', { name: 'On hold' }));
    await userEvent.type(screen.getByLabelText('Price'), '99.5');
    fireEvent.change(document.querySelector('input[type="date"]') as HTMLInputElement, { target: { value: '2026-12-01' } });
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.type(screen.getByPlaceholderText('https://...'), 'https://example.test/spec');
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    await userEvent.type(screen.getByPlaceholderText('e.g. 3mm magnet'), '3mm magnet');
    await toTab(/^Items/);
    await userEvent.click(await screen.findByTitle('Add Bracket.stl'));
    await userEvent.click(await screen.findByTitle('Add Widget.stl'));

    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));

    await waitFor(() => expect(where()).toBe('/projects/7/edit'));
    expect(api.to('POST', '/api/v1/projects')[0].body).toEqual({
      name: 'Vela order', customer: 'Vela Robotics', order_type: 'customer', on_hold: true, due_date: '2026-12-01',
      notes: 'rush', price: 99.5, customer_id: null,
    });
    expect(api.to('POST', '/api/v1/projects/7/items').map(c => (c.body as { file_id: number; sort_order: number })))
      .toEqual([expect.objectContaining({ file_id: 1, sort_order: 0 }), expect.objectContaining({ file_id: 2, sort_order: 1 })]);
    expect(api.to('POST', '/api/v1/projects/7/links').map(c => c.body)).toEqual([{ url: 'https://example.test/spec', label: null }]);
    expect(api.to('POST', '/api/v1/projects/7/parts').map(c => c.body)).toEqual([{ name: '3mm magnet', quantity: 1, allocated: false }]);
    expect(api.to('POST', '/api/v1/projects/7/generate')).toEqual([]);                  // saving never queues
  });

  it('sends a part\'s unit cost only when one was entered', async () => {
    const api = open('/projects/new');
    await userEvent.type(screen.getByLabelText(/Project name/), 'Cabinet');
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    await userEvent.type(screen.getByPlaceholderText('e.g. 3mm magnet'), 'magnet');
    await userEvent.type(screen.getByLabelText('Unit cost'), '0.4');
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    await userEvent.type(screen.getAllByPlaceholderText('e.g. 3mm magnet')[1], 'screw');         // no cost entered
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));

    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/parts')).toHaveLength(2));
    expect(api.to('POST', '/api/v1/projects/7/parts').map(c => c.body)).toEqual([
      { name: 'magnet', quantity: 1, allocated: false, unit_cost: 0.4 },
      { name: 'screw', quantity: 1, allocated: false },
    ]);
  });

  it('does not send a customer name for an internal project, even if one was typed then switched away', async () => {
    const api = open('/projects/new');
    await userEvent.type(screen.getByLabelText(/Project name/), 'Internal');
    const type = screen.getAllByRole('combobox').find(el => within(el as HTMLElement).queryByRole('option', { name: 'Customer' }))!;
    await userEvent.selectOptions(type, 'customer');
    await userEvent.type(screen.getByPlaceholderText('Customer name'), 'Vela Robotics');
    await userEvent.selectOptions(type, 'internal');
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));

    await waitFor(() => expect(api.to('POST', '/api/v1/projects')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/projects')[0].body).toMatchObject({ order_type: 'internal', customer: '', customer_id: null });
  });

  it('deletes a saved item that was removed, and Undo keeps it', async () => {
    const api = await existing();
    await userEvent.click(screen.getByTitle('Remove'));
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await userEvent.click(screen.getByTitle('Remove'));
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/projects/7/items/50')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/projects/7/items/50')).toEqual([]);
  });

  it('a retry after a save failed part-way does not delete the removed item again', async () => {
    let puts = 0, deletes = 0;
    const api = await existing(open('/projects/7/edit', {
      'GET /api/v1/projects/7': { ...EXISTING, items: [...EXISTING.items, { ...EXISTING.items[0], id: 49, file_id: 2, file_name: 'Widget.stl' }] },
      'DELETE /api/v1/projects/7/items/49': () => (++deletes === 1 ? { deleted: 49 } : new Reply(404, 'gone')),
      'PUT /api/v1/projects/7/items/50': () => (++puts === 1 ? new Reply(500, 'blip') : EXISTING.items[0]),
    }));
    await userEvent.click(screen.getAllByTitle('Remove')[1]);                       // Widget.stl (saved as item 49)
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    expect(await screen.findByText(/Save failed: 500 blip/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(puts).toBe(2));
    expect(deletes).toBe(1);
    await waitFor(() => expect(screen.queryByText(/Save failed/)).toBeNull());
    expect(api.to('DELETE', '/api/v1/projects/7/items/49')).toHaveLength(1);
  });

  it('cannot save without a name', async () => {
    open('/projects/new');
    const save = await screen.findByRole('button', { name: 'Save draft' });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(/Project name/), 'Named');
    expect((save as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('ProjectBuilderScreen - Review and Queue project', () => {
  it('saves pending edits on entering Review, runs the checks, then queues every group', async () => {
    const api = await existing();
    await userEvent.click(await screen.findByTitle('Add Widget.stl'));
    fireEvent.change(screen.getByLabelText('Quantity of Bracket.stl'), { target: { value: '5' } });
    await toTab(/^Review/);
    await screen.findByText('Every item is in a group');
    expect(writes(api)).toEqual(['PATCH /api/v1/projects/7', 'PUT /api/v1/projects/7/items/50', 'POST /api/v1/projects/7/items']);
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).toEqual({
      quantity: 5, filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 0,
      pack_rule: 'split', group_id: 1,
    });
    expect(api.to('POST', '/api/v1/projects/7/items')[0].body).toEqual({
      file_id: 2, quantity: 1, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 1,
      kind: 'stl', source_ref: null, pack_rule: 'split',
    });

    await waitFor(() => expect(queueBtn().disabled).toBe(false));
    await userEvent.click(queueBtn());
    await screen.findByText('2 jobs added to queue');
    expect(api.to('POST', '/api/v1/projects/7/generate')[0].body).toEqual({   // eligibility comes from the groups
      eligible_printer_ids: [], eligible_model_uuids: [], eligible_any_printer: false, process_preset: null, allow_cached: true, save_slice: false,
    });
    expect(api.to('POST', '/api/v1/projects/7/items')).toHaveLength(1);       // the new row was saved once, not again
    expect(api.to('PUT', '/api/v1/projects/7/items/51')).toHaveLength(1);    // ...and updated from then on
    expect(where()).toBe('/projects/7/edit');
  });

  it('keeps Queue project disabled while a check is open, and Go to Groups opens the group that fixes it', async () => {
    await existing(open('/projects/7/edit', {
      'GET /api/v1/projects/7/review': { open: 1, checks: [
        { level: 'error', tab: 'groups', message: 'Fine: choose its eligible printers', group_id: 2, item_id: null },
        { level: 'note', tab: 'items', message: 'Bracket.stl is too big for Printer B', group_id: 1, item_id: 50 },
      ] },
    }));
    await toTab(/^Review/);
    await screen.findByText('Fine: choose its eligible printers');
    expect(screen.getByRole('tab', { name: /^Review/ }).textContent).toContain('1 to fix');
    expect(screen.getByText('Fix 1 issue to queue')).toBeTruthy();
    expect(queueBtn().disabled).toBe(true);
    expect(screen.getAllByRole('button', { name: /Go to/ })).toHaveLength(1);       // notes need no fix

    await userEvent.click(screen.getByRole('button', { name: 'Go to Groups' }));
    expect(screen.getByRole('tab', { name: /^Groups/ }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('badge-2').getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByRole('tab', { name: /^All/ })).toBeNull();                 // the Groups tab has no All
  });

  it('shows group warnings with the result and opens the project or the queue from it', async () => {
    await existing(open('/projects/7/edit', {
      'POST /api/v1/projects/7/generate': generated(1, { groups: [{ id: 2, warning: 'Fine: no printer can take these jobs' }] }),
    }));
    await reviewAndQueue();
    await screen.findByText('1 job added to queue');
    expect(screen.getByText('Fine: no printer can take these jobs')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Queue' }));
    expect(where()).toBe('/queue');
  });

  it('opens the project from the result banner', async () => {
    await existing();
    await reviewAndQueue();
    await screen.findByText('2 jobs added to queue');
    await userEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(where()).toBe('/projects/7');
  });

  it('does not queue when saving the edits fails, and says so', async () => {
    const api = await existing(open('/projects/7/edit', { 'PATCH /api/v1/projects/7': new Reply(500, 'disk full') }));
    fireEvent.change(screen.getByLabelText('Quantity of Bracket.stl'), { target: { value: '3' } });
    await toTab(/^Review/);
    expect(await screen.findByText(/Save failed: 500 disk full/)).toBeTruthy();
    expect(api.to('GET', '/api/v1/projects/7/review')).toEqual([]);
    expect(queueBtn().disabled).toBe(true);
  });

  it('Retry queues again after a failure', async () => {
    let attempts = 0;
    const api = await existing(open('/projects/7/edit', {
      'POST /api/v1/projects/7/generate': () => (++attempts === 1 ? new Reply(504, 'slow') : generated(1)),
    }));
    await reviewAndQueue();
    await screen.findByText('Generation timed out. Try fewer parts or reduce quantities.');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('1 job added to queue');
    expect(api.to('POST', '/api/v1/projects/7/generate')).toHaveLength(2);
  });
});

describe('ProjectBuilderScreen - review fixes (BIZ-363)', () => {
  it('a new link and part are added once, then updated, however often the Builder saves', async () => {
    const api = await existing(open('/projects/7/edit', {
      'PUT /api/v1/projects/7/links/70': { id: 70 }, 'PUT /api/v1/projects/7/parts/80': { id: 80 },
    }));
    await userEvent.click(screen.getByRole('tab', { name: /^Details/ }));
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.type(screen.getByPlaceholderText('https://...'), 'https://x.test');
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    await userEvent.type(screen.getByPlaceholderText('e.g. 3mm magnet'), 'magnet');
    await reviewAndQueue();                                                  // Review saves, then Queue saves again
    await screen.findByText('2 jobs added to queue');
    expect(api.to('POST', '/api/v1/projects/7/links')).toHaveLength(1);
    expect(api.to('POST', '/api/v1/projects/7/parts')).toHaveLength(1);
    expect(api.to('PUT', '/api/v1/projects/7/links/70')).toHaveLength(1);
    expect(api.to('PUT', '/api/v1/projects/7/parts/80')).toHaveLength(1);
  });

  it('runs the checks once per Review visit', async () => {
    const api = await existing();
    fireEvent.change(screen.getByLabelText('Quantity of Bracket.stl'), { target: { value: '3' } });
    await toTab(/^Review/);
    await screen.findByText('Every item is in a group');
    await waitFor(() => expect(queueBtn().disabled).toBe(false));
    expect(api.to('GET', '/api/v1/projects/7/review')).toHaveLength(1);
  });

  it('a failed check run leaves Queue disabled instead of trusting the last result', async () => {
    let calls = 0;
    await existing(open('/projects/7/edit', {
      'GET /api/v1/projects/7/review': () => (++calls === 1 ? CLEAN : new Reply(500, 'boom')),
    }));
    await toTab(/^Review/);
    await waitFor(() => expect(queueBtn().disabled).toBe(false));
    await toTab(/^Items/);
    fireEvent.change(screen.getByLabelText('Quantity of Bracket.stl'), { target: { value: '9' } });
    await toTab(/^Review/);
    expect(await screen.findByText(/Couldn't run the checks/)).toBeTruthy();
    expect(queueBtn().disabled).toBe(true);
  });

  it('items of a group that was deleted move to the Default Group and save without it', async () => {
    let groups = [group(1, 'Default Group'), group(2, 'Fine')];
    const api = open('/projects/7/edit', {
      'GET /api/v1/projects/7': { ...EXISTING, items: [{ ...EXISTING.items[0], group_id: 2 }] },
      'GET /api/v1/projects/7/groups': () => groups,
      'DELETE /api/v1/projects/7/groups/2': () => { groups = [group(1, 'Default Group')]; return new Reply(204); },
    });
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await waitFor(() => expect(screen.getByTestId('badge-2').textContent).toContain('1 item'));
    await toTab(/^Groups/);
    await userEvent.click(screen.getByTestId('badge-2'));
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByTestId('badge-2')).toBeNull());
    expect(screen.getByTestId('badge-1').textContent).toContain('1 item');
    await toTab(/^Items/);
    fireEvent.change(screen.getByLabelText('Quantity of Bracket.stl'), { target: { value: '4' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/items/50')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).not.toHaveProperty('group_id');
  });

  it('a draft is promoted to planning from the Details tab', async () => {
    const api = open('/projects/7/edit', {
      'GET /api/v1/projects/7': { ...EXISTING, stage: 'draft' },
      'POST /api/v1/projects/7/promote': { ok: true },
    });
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await toTab(/^Details/);
    await userEvent.click(screen.getByRole('button', { name: 'Promote to planning' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/promote')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/projects/7/promote')[0].body).toEqual({ stage: 'planning' });
    expect(screen.queryByRole('button', { name: 'Promote to planning' })).toBeNull();
  });
});

describe('ProjectBuilderScreen - queue errors', () => {
  it.each([
    [422, 'nothing to pack', 'Add at least one part before generating.'],
    [502, 'bad gateway', 'Orca sidecar is offline. Check the container.'],
    [504, 'timeout', 'Generation timed out. Try fewer parts or reduce quantities.'],
    [500, 'no STL data for file 3', 'One or more STL files are missing. Remove and re-add them.'],
  ])('%i is explained in plain words', async (status, body, message) => {
    await existing(open('/projects/7/edit', { 'POST /api/v1/projects/7/generate': new Reply(status, body) }));
    await reviewAndQueue();
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByText('2 jobs added to queue')).toBeNull();
  });

  it('shows the server response for a status it has no wording for', async () => {
    await existing(open('/projects/7/edit', {
      'POST /api/v1/projects/7/generate': new Reply(409, { detail: 'Promote the project to planning before creating jobs' }),
    }));
    await reviewAndQueue();
    expect(await screen.findByText(
      'Generation failed: 409 {"detail":"Promote the project to planning before creating jobs"}')).toBeTruthy();
  });
});
