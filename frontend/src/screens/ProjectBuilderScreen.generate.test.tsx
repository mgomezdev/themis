import { render, screen, waitFor } from '@testing-library/react';
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
    await screen.findByRole('option', { name: '0.28mm Draft' }).catch(() => null);
    await waitFor(() => expect(screen.getByTestId('process-preset-select').textContent).toContain('0.20mm Standard'));
    await userEvent.selectOptions(screen.getByTestId('process-preset-select'), '0.20mm Standard');
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));

    await screen.findByText('2 jobs added to queue');
    expect(writes(api)).toEqual(['POST /api/v1/projects', 'POST /api/v1/projects/7/items', 'POST /api/v1/projects/7/generate']);
    expect(api.to('POST', '/api/v1/projects')[0].body).toEqual({
      name: 'Shelf set', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
      amount_paid: null, payment_status: 'unpaid',
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
    expect(api.to('POST', '/api/v1/projects/7/generate')).toHaveLength(2);
  });
});
