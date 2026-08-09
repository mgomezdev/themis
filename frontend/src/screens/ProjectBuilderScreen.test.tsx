import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';

const FILES = [
  { id: 1, original_filename: 'Bracket.stl', relative_path: 'Bracket.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
  { id: 2, original_filename: 'Widget.stl', relative_path: 'Widget.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
];

const PRINTERS = [
  { id: 1, name: 'Printer A', printer_type: 'bambu_x1c', connection_config: {}, awaiting_plate_clear: false, orca_printer_profiles: [], current_orca_printer_profile: null, enabled: true, queue_on: true, connected: true, loaded_filaments: [], build_plate_type: null, no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256 },
];

function jsonResponse(data: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
}

const mockFetch = vi.fn((input: unknown) => {
  const url = String(input);
  if (url.startsWith('/api/v1/files')) return jsonResponse(FILES);
  if (url === '/api/v1/settings/spoolman') return jsonResponse({ enabled: false });
  if (url === '/api/v1/printers') return jsonResponse(PRINTERS);
  return jsonResponse({});
});
vi.stubGlobal('fetch', mockFetch);
beforeEach(() => mockFetch.mockClear());

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter initialEntries={['/projects/new']}>{children}</MemoryRouter>
);

/** Filament type <select> has an "any" option; the order-type select does not. */
function filamentSelects() {
  return screen.getAllByRole('combobox').filter(el =>
    within(el as HTMLElement).queryByRole('option', { name: 'any' }),
  ) as HTMLSelectElement[];
}

describe('ProjectBuilderScreen', () => {
  it('bumps quantity instead of adding a duplicate row when the same file is clicked twice', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    const addBtn = await screen.findByTitle('Add Bracket.stl');
    await user.click(addBtn);
    await user.click(addBtn);

    expect(screen.getAllByTitle('Remove')).toHaveLength(1);
    expect(screen.getByRole('spinbutton')).toHaveValue(2);
  });

  it('filters the STL list by filename when searching', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await screen.findByTitle('Add Bracket.stl');
    await user.type(screen.getByPlaceholderText('Search files…'), 'brack');

    expect(screen.getByTitle('Add Bracket.stl')).toBeTruthy();
    expect(screen.queryByTitle('Add Widget.stl')).toBeNull();
  });

  it('applies one part\'s filament choice to every other part', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(await screen.findByTitle('Add Widget.stl'));

    const [firstSelect, secondSelect] = filamentSelects();
    await user.selectOptions(firstSelect, 'PETG');
    await user.click(screen.getAllByTitle('Apply this filament to every part')[0]);

    expect(secondSelect).toHaveValue('PETG');
  });

  it('restores a removed part when Undo is clicked', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(screen.getByTitle('Remove'));

    expect(screen.queryByTitle('Remove')).toBeNull();
    expect(screen.getByText(/Removed Bracket\.stl/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByTitle('Remove')).toBeTruthy();
    expect(screen.getByRole('spinbutton')).toHaveValue(1);
  });

  it('labels Generate as "without dispatch" when no printers are selected', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await user.type(screen.getByLabelText(/Project name/), 'Test Project');
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(screen.getByRole('button', { name: 'Generate…' }));

    await screen.findByText('Printer A');
    expect(screen.getByRole('button', { name: 'Generate without dispatch' })).toBeTruthy();
  });
});
