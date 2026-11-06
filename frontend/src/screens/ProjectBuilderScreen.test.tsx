import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';

const FILES = [
  { id: 1, original_filename: 'Bracket.stl', relative_path: 'Bracket.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
  { id: 2, original_filename: 'Widget.stl', relative_path: 'Widget.stl', folder: '', size_bytes: 1, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
];

const PRINTERS = [
  { id: 1, name: 'Printer A', printer_type: 'bambu_x1c', connection_config: {}, awaiting_plate_clear: false, orca_printer_profiles: [], machine_profile_override: null, enabled: true, queue_on: true, connected: true, loaded_filaments: [], build_plate_type: null, no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256 },
];

function jsonResponse(data: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
}

const mockFetch = vi.fn((input: unknown, _init?: RequestInit) => {
  const url = String(input);
  if (url.startsWith('/api/v1/files')) return jsonResponse(FILES);
  if (url === '/api/v1/plugins') return jsonResponse({ plugins: [], selections: {} });
  if (url === '/api/v1/printers') return jsonResponse(PRINTERS);
  return jsonResponse({});
});
vi.stubGlobal('fetch', mockFetch);
beforeEach(() => mockFetch.mockClear());

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter initialEntries={['/projects/new']}>{children}</MemoryRouter>
);

/** One filament-type <select> per part (the editor's own `item-ask-*-type` control). */
function typeSelects() {
  return screen.getAllByLabelText('Filament type') as HTMLSelectElement[];
}
const toTab = (name: RegExp) => userEvent.click(screen.getByRole('tab', { name }));

describe('ProjectBuilderScreen', () => {
  it('bumps quantity instead of adding a duplicate row when the same file is clicked twice', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await toTab(/^Items/);
    const addBtn = await screen.findByTitle('Add Bracket.stl');
    await user.click(addBtn);
    await user.click(addBtn);

    expect(screen.getAllByTitle('Remove')).toHaveLength(1);
    expect(screen.getByLabelText('Quantity of Bracket.stl')).toHaveValue(2);
  });

  it('filters the STL list by filename when searching', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await toTab(/^Items/);
    await screen.findByTitle('Add Bracket.stl');
    await user.type(screen.getByPlaceholderText('Search files…'), 'brack');

    expect(screen.getByTitle('Add Bracket.stl')).toBeTruthy();
    expect(screen.queryByTitle('Add Widget.stl')).toBeNull();
  });

  it('applies one part\'s filament choice to every other part', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await toTab(/^Items/);
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(await screen.findByTitle('Add Widget.stl'));

    const [firstType, secondType] = typeSelects();
    await user.type(screen.getAllByLabelText('Manufacturer')[0], 'Acme');
    await user.selectOptions(firstType, 'PETG');
    await user.selectOptions(screen.getAllByLabelText('Filament subtype')[0], 'silk');
    await user.click(screen.getAllByTitle('Apply this filament to every part')[0]);

    expect(secondType).toHaveValue('PETG');                                            // the whole tuple is copied, not just the type
    expect(screen.getAllByLabelText('Filament subtype')[1]).toHaveValue('silk');
    expect(screen.getAllByLabelText('Manufacturer')[1]).toHaveValue('Acme');
  });

  it('restores a removed part when Undo is clicked', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await toTab(/^Items/);
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(screen.getByTitle('Remove'));

    expect(screen.queryByTitle('Remove')).toBeNull();
    expect(screen.getByText(/Removed Bracket\.stl/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByTitle('Remove')).toBeTruthy();
    expect(screen.getByLabelText('Quantity of Bracket.stl')).toHaveValue(1);
  });

  it('keeps everything typed when moving between tabs, and counts items on the Items tab', async () => {
    const user = userEvent.setup();
    render(<ProjectBuilderScreen />, { wrapper });
    await user.type(screen.getByLabelText(/Project name/), 'Shelf set');
    await user.click(screen.getByRole('button', { name: 'Next →' }));                    // Details → Items
    await user.click(await screen.findByTitle('Add Bracket.stl'));
    await user.click(await screen.findByTitle('Add Widget.stl'));
    expect(screen.getByRole('tab', { name: /^Items/ }).textContent).toContain('2');
    await toTab(/^Details/);
    expect(screen.getByLabelText(/Project name/)).toHaveValue('Shelf set');
    await user.click(screen.getByRole('button', { name: 'Next →' }));
    expect(screen.getAllByTitle('Remove')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: '← Back' }));
    expect(screen.getByLabelText(/Project name/)).toHaveValue('Shelf set');
  });

  it('asks to save the draft before setting up groups or reviewing a new project', async () => {
    render(<ProjectBuilderScreen />, { wrapper });
    await toTab(/^Groups/);
    expect(screen.getByText(/Save the draft to set up its print groups/)).toBeTruthy();
    await toTab(/^Review/);
    expect(screen.getByText(/Save the draft to check and queue it/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Queue project' }) as HTMLButtonElement).disabled).toBe(true);
    await toTab(/^Items/);
    expect((screen.getByTestId('badge-new') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('ProjectBuilderScreen draft stage', () => {
  it('keeps Queue project disabled for a draft project', async () => {
    const { Routes, Route } = await import('react-router-dom');
    mockFetch.mockImplementation((input: unknown) => {
      const url = String(input);
      if (url === '/api/v1/projects/5') return jsonResponse({
        id: 5, name: 'Customer Request', customer: '', order_type: 'customer', on_hold: false, due_date: null,
        notes: null, amount_paid: null, price: null, payment_status: 'unpaid', stage: 'draft',
        customer_id: null, customer_name: null, links: [], parts: [],
        items: [{ id: 1, file_id: 1, file_name: 'Bracket.stl', quantity: 1, quantity_completed: 0, quantity_failed: 0,
                  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0 }],
      });
      if (url === '/api/v1/projects/5/review') return jsonResponse({ checks: [], open: 0 });     // even with nothing open
      if (url.startsWith('/api/v1/files')) return jsonResponse(FILES);
      if (url === '/api/v1/plugins') return jsonResponse({ plugins: [], selections: {} });
      return jsonResponse([]);
    });
    render(
      <MemoryRouter initialEntries={['/projects/5/edit']}>
        <Routes><Route path="/projects/:id/edit" element={<ProjectBuilderScreen />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByTitle('Remove');
    await toTab(/^Review/);
    await vi.waitFor(() => expect(mockFetch.mock.calls.some(c => String(c[0]) === '/api/v1/projects/5/review')).toBe(true));
    const btn = screen.getByRole('button', { name: 'Queue project' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('Promote to planning before creating jobs');
  });
});

describe('ProjectBuilderScreen file drop', () => {
  it('uploads a file dropped on the Builder and adds it as a part', async () => {
    const { fireEvent, waitFor } = await import('@testing-library/react');
    const base = mockFetch.getMockImplementation()!;
    mockFetch.mockImplementation((input: unknown, init?: RequestInit) =>
      String(input) === '/api/v1/files/upload' && init?.method === 'POST'
        ? jsonResponse({ ...FILES[0], id: 9, original_filename: 'Dropped.stl', relative_path: 'Dropped.stl', kind: 'stl' })
        : base(input));
    try {
      render(<ProjectBuilderScreen />, { wrapper });
      await toTab(/^Items/);
      await screen.findByTitle('Add Bracket.stl');
      fireEvent.drop(screen.getByTestId('file-drop-zone'),
                     { dataTransfer: { files: [new File(['x'], 'Dropped.stl')], types: ['Files'] } });

      await waitFor(() => expect(screen.getByLabelText('Quantity of Dropped.stl')).toHaveValue(1));
      expect((mockFetch.mock.calls as unknown[][]).some(([u, i]) => u === '/api/v1/files/upload' && (i as RequestInit | undefined)?.method === 'POST')).toBe(true);
    } finally {
      mockFetch.mockImplementation(base);
    }
  });
});
