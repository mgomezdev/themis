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
  current_orca_printer_profile: null, enabled: true, queue_on: true, connected: true, loaded_filaments: [],
  build_plate_type: null, no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256, ...extra,
});
const PRINTERS = [printer(1, 'Printer A'), printer(2, 'Printer B'), printer(3, 'Retired', { enabled: false })];

const EXISTING = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  amount_paid: null, payment_status: 'unpaid', stage: 'planning', customer_id: null, links: [], parts: [],
  items: [{ id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 2, quantity_completed: 0, quantity_failed: 0,
            filament_type: 'PLA', filament_color: 'any', filament_id: null, sort_order: 0 }],
};
const job = (id: number) => ({ id, uploaded_file_id: 1, plate_number: 1, queue_position: id, status: 'queued' });
const generated = (n: number) => ({ project_id: 7, jobs: Array.from({ length: n }, (_, i) => job(i + 1)), files: [], eligible_printer_ids: [], pack_bed_x: 256, pack_bed_y: 256 });

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }

/** The builder routed as in App.tsx, so post-save navigation behaves like the real app. */
function open(path: string, over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/files': FILES,
    'GET /api/v1/settings/spoolman': { enabled: false },
    'GET /api/v1/printers': PRINTERS,
    'GET /api/v1/printers/1/profiles': { print_profiles: ['0.20mm Standard', '0.12mm Fine'], filament_profiles: [] },
    'GET /api/v1/printers/2/profiles': { print_profiles: ['0.20mm Standard', '0.28mm Draft'], filament_profiles: [] },
    'GET /api/v1/projects/7': EXISTING,
    'POST /api/v1/projects': { ...EXISTING, id: 7, items: [] },
    'POST /api/v1/projects/7/items': { id: 51 },
    'POST /api/v1/projects/7/links': { id: 70 },
    'POST /api/v1/projects/7/parts': { id: 80 },
    'PATCH /api/v1/projects/7': EXISTING,
    'PUT /api/v1/projects/7/items/50': EXISTING.items[0],
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

async function newProjectWithBracket(name = 'Shelf set') {
  await userEvent.type(screen.getByLabelText(/Project name/), name);
  await userEvent.click(await screen.findByTitle('Add Bracket.stl'));
}
async function openPicker() {
  await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
  await screen.findByText('Printer A');
}

afterEach(() => vi.unstubAllGlobals());

describe('ProjectBuilderScreen - generating a new project', () => {
  it('saves the project and its parts, then generates for the chosen printers and preset', async () => {
    const api = open('/projects/new');
    await newProjectWithBracket();
    await openPicker();

    await userEvent.click(screen.getByRole('checkbox', { name: /Printer A/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Printer B/ }));
    await waitFor(() => expect(screen.getByTestId('process-preset-select').textContent).toContain('0.20mm Standard'));
    await userEvent.selectOptions(screen.getByTestId('process-preset-select'), '0.20mm Standard');
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));

    await screen.findByText('2 jobs added to queue');
    expect(screen.queryByText('Eligible printers')).toBeNull();      // the picker does not pop back open when generation ends
    expect(writes(api)).toEqual(['POST /api/v1/projects', 'POST /api/v1/projects/7/items', 'POST /api/v1/projects/7/generate']);
    expect(api.to('POST', '/api/v1/projects')[0].body).toEqual({
      name: 'Shelf set', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
      amount_paid: null, price: null, payment_status: 'unpaid', customer_id: null,
    });
    expect(api.to('POST', '/api/v1/projects/7/items')[0].body).toEqual({
      file_id: 1, quantity: 1, filament_type: 'any', filament_color: 'any', filament_id: null, sort_order: 0,
    });
    expect(api.to('POST', '/api/v1/projects/7/generate')[0].body).toEqual({
      eligible_printer_ids: [1, 2], process_preset: '0.20mm Standard',
    });
  });

  it('only offers enabled printers and shows the smallest selected bed as the pack size', async () => {
    open('/projects/new', { 'GET /api/v1/printers': [...PRINTERS.slice(0, 2), printer(4, 'Mini', { bed_x_mm: 180, bed_y_mm: 200 })],
                            'GET /api/v1/printers/4/profiles': { print_profiles: [], filament_profiles: [] } });
    await newProjectWithBracket();
    await openPicker();

    expect(screen.queryByText('Retired')).toBeNull();
    await userEvent.click(screen.getByRole('checkbox', { name: /Printer A/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Mini/ }));
    expect(screen.getByText('Pack bed: 180×200 mm (smallest selected)')).toBeTruthy();
  });

  it('generates without dispatch (no printers, no preset) when none is selected', async () => {
    const api = open('/projects/new', { 'POST /api/v1/projects/7/generate': generated(1) });
    await newProjectWithBracket();
    await openPicker();

    await userEvent.click(screen.getByRole('button', { name: 'Generate without dispatch' }));

    await screen.findByText('1 job added to queue');
    expect(api.to('POST', '/api/v1/projects/7/generate')[0].body).toEqual({ eligible_printer_ids: [], process_preset: null });
  });

  it('keeps the builder on screen so the result is visible, and lets Retry reuse the saved project', async () => {
    let attempts = 0;
    const api = open('/projects/new', {
      'POST /api/v1/projects/7/generate': () => (++attempts === 1 ? new Reply(502, 'sidecar down') : generated(1)),
    });
    await newProjectWithBracket();
    await openPicker();
    await userEvent.click(screen.getByRole('button', { name: 'Generate without dispatch' }));

    expect(await screen.findByText('Orca sidecar is offline. Check the container.')).toBeTruthy();
    expect(where()).not.toBe('/projects/7');                                   // not bounced to the detail page
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('1 job added to queue')).toBeTruthy();
    expect(screen.queryByText(/Orca sidecar is offline/)).toBeNull();
    expect(api.to('POST', '/api/v1/projects')).toHaveLength(1);                // retry must not create a second project
    expect(api.to('POST', '/api/v1/projects/7/items')).toHaveLength(1);        // ...nor add the part a second time
    expect(api.to('PUT', '/api/v1/projects/7/items/50')).toHaveLength(1);      // it updates the saved part instead
    expect(api.to('POST', '/api/v1/projects/7/generate')).toHaveLength(2);
  });
});

describe('ProjectBuilderScreen - what gets saved before generating', () => {
  it('sends every header field, part order, links and non-printed parts for a customer project', async () => {
    const api = open('/projects/new');
    await userEvent.type(screen.getByLabelText(/Project name/), 'Vela order');
    await userEvent.selectOptions(screen.getAllByRole('combobox').find(el => within(el as HTMLElement).queryByRole('option', { name: 'Customer' }))!, 'customer');
    await userEvent.type(screen.getByPlaceholderText('Customer name'), 'Vela Robotics');
    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'rush');
    await userEvent.click(screen.getByRole('checkbox', { name: 'On hold' }));
    await userEvent.type(screen.getByLabelText('Price'), '99.5');
    await userEvent.type(screen.getByPlaceholderText('0.00'), '12.5');
    await userEvent.selectOptions(screen.getByDisplayValue('Unpaid'), 'partial');
    fireEvent.change(document.querySelector('input[type="date"]') as HTMLInputElement, { target: { value: '2026-12-01' } });
    await userEvent.click(await screen.findByTitle('Add Bracket.stl'));
    await userEvent.click(await screen.findByTitle('Add Widget.stl'));
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.type(screen.getByPlaceholderText('https://...'), 'https://example.test/spec');
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    await userEvent.type(screen.getByPlaceholderText('e.g. 3mm magnet'), '3mm magnet');

    await openPicker();
    await userEvent.click(screen.getByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('2 jobs added to queue');

    expect(api.to('POST', '/api/v1/projects')[0].body).toEqual({
      name: 'Vela order', customer: 'Vela Robotics', order_type: 'customer', on_hold: true, due_date: '2026-12-01',
      notes: 'rush', amount_paid: 12.5, price: 99.5, payment_status: 'partial', customer_id: null,   // typed name, no account
    });
    expect(api.to('POST', '/api/v1/projects/7/items').map(c => (c.body as { file_id: number; sort_order: number })))
      .toEqual([expect.objectContaining({ file_id: 1, sort_order: 0 }), expect.objectContaining({ file_id: 2, sort_order: 1 })]);
    expect(api.to('POST', '/api/v1/projects/7/links').map(c => c.body)).toEqual([{ url: 'https://example.test/spec', label: null }]);
    expect(api.to('POST', '/api/v1/projects/7/parts').map(c => c.body)).toEqual([{ name: '3mm magnet', quantity: 1, allocated: false }]);
  });

  it('does not send a customer name for an internal project, even if one was typed then switched away', async () => {
    const api = open('/projects/new');
    await userEvent.type(screen.getByLabelText(/Project name/), 'Internal');
    const type = screen.getAllByRole('combobox').find(el => within(el as HTMLElement).queryByRole('option', { name: 'Customer' }))!;
    await userEvent.selectOptions(type, 'customer');
    await userEvent.type(screen.getByPlaceholderText('Customer name'), 'Vela Robotics');
    await userEvent.selectOptions(type, 'internal');
    await userEvent.click(await screen.findByTitle('Add Bracket.stl'));
    await openPicker();

    await userEvent.click(screen.getByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('2 jobs added to queue');

    expect(api.to('POST', '/api/v1/projects')[0].body).toMatchObject({ order_type: 'internal', customer: '', customer_id: null });
  });

  it('Retry generates for the same printers again', async () => {
    let attempts = 0;
    const api = open('/projects/new', {
      'POST /api/v1/projects/7/generate': () => (++attempts === 1 ? new Reply(504, 'slow') : generated(1)),
    });
    await newProjectWithBracket();
    await openPicker();
    await userEvent.click(screen.getByRole('checkbox', { name: /Printer B/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await screen.findByText('Generation timed out. Try fewer parts or reduce quantities.');

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await screen.findByText('1 job added to queue');
    expect(api.to('POST', '/api/v1/projects/7/generate').map(c => c.body)).toEqual([
      { eligible_printer_ids: [2], process_preset: null }, { eligible_printer_ids: [2], process_preset: null },
    ]);
  });
});

describe('ProjectBuilderScreen - generating an existing project', () => {
  it('saves edits first (project, existing part, new part) and then generates', async () => {
    const api = open('/projects/7/edit');
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await userEvent.click(await screen.findByTitle('Add Widget.stl'));
    const spinbuttons = screen.getAllByRole('spinbutton');
    const bracketQty = spinbuttons[spinbuttons.length - 2];   // item rows are last; "Amount paid" is a spinbutton too
    fireEvent.change(bracketQty, { target: { value: '5' } });   // (clearing snaps the field back to 1, so type-over would give 15)

    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    await screen.findByText('2 jobs added to queue');
    expect(writes(api)).toEqual([
      'PATCH /api/v1/projects/7', 'PUT /api/v1/projects/7/items/50',
      'POST /api/v1/projects/7/items', 'POST /api/v1/projects/7/generate',
    ]);
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).toEqual({
      quantity: 5, filament_type: 'PLA', filament_color: 'any', filament_id: null, sort_order: 0,
    });
    expect(api.to('POST', '/api/v1/projects/7/items')[0].body).toEqual({
      file_id: 2, quantity: 1, filament_type: 'any', filament_color: 'any', filament_id: null, sort_order: 1,
    });
    expect(where()).toBe('/projects/7/edit');
  });

  it('opens the project from the result banner', async () => {
    open('/projects/7/edit');
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('2 jobs added to queue');

    await userEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(where()).toBe('/projects/7');
  });

  it('opens the queue from the result banner', async () => {
    open('/projects/7/edit');
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));
    await screen.findByText('2 jobs added to queue');

    await userEvent.click(screen.getByRole('button', { name: 'Queue' }));
    expect(where()).toBe('/queue');
  });

  it('does not generate when saving the edits fails, and says so', async () => {
    const api = open('/projects/7/edit', { 'PATCH /api/v1/projects/7': new Reply(500, 'disk full') });
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));

    expect(await screen.findByText('Generation failed: 500 disk full')).toBeTruthy();
    expect(api.to('POST', '/api/v1/projects/7/generate')).toEqual([]);
  });
});

describe('ProjectBuilderScreen - generate errors', () => {
  async function failWith(reply: Reply) {
    const api = open('/projects/7/edit', { 'POST /api/v1/projects/7/generate': reply });
    await screen.findByText('Bracket.stl', { selector: 'span[title="Bracket.stl"]' });
    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Generate without dispatch' }));
    return api;
  }

  it.each([
    [422, 'nothing to pack', 'Add at least one part before generating.'],
    [502, 'bad gateway', 'Orca sidecar is offline. Check the container.'],
    [504, 'timeout', 'Generation timed out. Try fewer parts or reduce quantities.'],
    [500, 'no STL data for file 3', 'One or more STL files are missing. Remove and re-add them.'],
  ])('%i is explained in plain words', async (status, body, message) => {
    await failWith(new Reply(status, body));

    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByText('2 jobs added to queue')).toBeNull();
  });

  it('shows the server response for a status it has no wording for (a draft project gets a raw 409)', async () => {
    await failWith(new Reply(409, { detail: 'Promote the project to planning before creating jobs' }));

    expect(await screen.findByText(
      'Generation failed: 409 {"detail":"Promote the project to planning before creating jobs"}')).toBeTruthy();
  });

  it('clears the error when the picker is reopened', async () => {
    await failWith(new Reply(502, 'down'));
    await screen.findByText('Orca sidecar is offline. Check the container.');

    await userEvent.click(screen.getByRole('button', { name: 'Generate…' }));

    expect(screen.queryByText('Orca sidecar is offline. Check the container.')).toBeNull();
  });
});

describe('ProjectBuilderScreen - generate guards', () => {
  it('cannot generate without a name or without parts', async () => {
    open('/projects/new');
    const generate = await screen.findByRole('button', { name: 'Generate…' });
    expect(generate.hasAttribute('disabled')).toBe(true);           // nothing yet

    await userEvent.type(screen.getByLabelText(/Project name/), 'Named only');
    expect(generate.hasAttribute('disabled')).toBe(true);           // no parts

    await userEvent.click(await screen.findByTitle('Add Bracket.stl'));
    expect(generate.hasAttribute('disabled')).toBe(false);

    await userEvent.clear(screen.getByLabelText(/Project name/));
    expect(generate.hasAttribute('disabled')).toBe(true);           // parts but no name
  });

  it('Cancel closes the printer picker without generating', async () => {
    const api = open('/projects/new');
    await newProjectWithBracket();
    await openPicker();

    await userEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);   // the picker's, before the footer's

    expect(screen.queryByText('Eligible printers')).toBeNull();
    expect(writes(api)).toEqual([]);
  });
});
