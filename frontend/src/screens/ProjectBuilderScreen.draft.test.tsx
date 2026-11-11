// Characterization tests for the builder's local draft state: dirty tracking, undo of removals, and the
// STL folder tree. Written before the ProjectBuilderScreen refactor (docs/superpowers/plans/
// 2026-11-11-project-builder-refactor.md) so every step of it is held to the same screen behavior.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';
import { stubFetch } from '../test/fetchStub';

const file = (id: number, name: string, folder = '', extra: object = {}) => ({
  id, original_filename: name, relative_path: name, folder, size_bytes: 1, plate_count: 1, uploaded_at: '',
  missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [], ...extra,
});
const FILES = [file(1, 'Bracket.stl'), file(2, 'Widget.stl')];

const EXISTING = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  amount_paid: null, payment_status: 'unpaid', stage: 'planning', customer_id: null, customer_name: null, price: null,
  links: [{ id: 70, project_id: 7, url: 'https://example.test/spec', label: 'Spec', sort_order: 0, created_at: '' }],
  parts: [{ id: 80, project_id: 7, name: '3mm magnet', quantity: 4, allocated: false, unit_cost: 0.25, sort_order: 0, created_at: '' }],
  items: [
    { id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 2, quantity_completed: 0, quantity_failed: 0,
      filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, sort_order: 0 },
    { id: 51, project_id: 7, file_id: 2, file_name: 'Widget.stl', quantity: 1, quantity_completed: 0, quantity_failed: 0,
      filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 1 },
  ],
};

function open(path: string, over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/files': FILES,
    'GET /api/v1/plugins': { plugins: [], selections: {} },
    'GET /api/v1/printers': [],
    'GET /api/v1/customers': [],
    'GET /api/v1/projects/7': EXISTING,
    'PATCH /api/v1/projects/7': EXISTING,
    'PUT /api/v1/projects/7/items/50': EXISTING.items[0],
    'PUT /api/v1/projects/7/items/51': EXISTING.items[1],
    'PUT /api/v1/projects/7/links/70': EXISTING.links[0],
    'DELETE /api/v1/projects/7/links/70': null,
    'PUT /api/v1/projects/7/parts/80': EXISTING.parts[0],
    'DELETE /api/v1/projects/7/parts/80': null,
    ...over,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/new" element={<ProjectBuilderScreen />} />
        <Route path="/projects/:id/edit" element={<ProjectBuilderScreen />} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}
const writes = (api: ReturnType<typeof stubFetch>) =>
  api.calls.filter(c => c.method !== 'GET').map(c => `${c.method} ${c.url}`);
const saveChanges = () => screen.queryByRole('button', { name: 'Save changes' });
/** File names of the project's part-item rows, in order. */
const itemRows = () => screen.queryAllByText(/\.stl$/, { selector: 'span[title]' }).map(e => e.textContent);
/** Label of every file button in the STL picker, in order. */
const pickerFiles = () => screen.queryAllByTitle(/^Add /).map(e => e.getAttribute('title')!.slice(4));

async function loaded() {
  await screen.findByDisplayValue('Gridfinity');
  await screen.findByDisplayValue('https://example.test/spec');
}

afterEach(() => vi.unstubAllGlobals());

describe('ProjectBuilderScreen - dirty tracking (edit mode)', () => {
  it('offers Save only while the draft differs from what was loaded or last saved', async () => {
    const api = open('/projects/7/edit');
    await loaded();
    expect(saveChanges()).toBeNull();

    const notes = screen.getByPlaceholderText('Optional notes');
    await userEvent.type(notes, 'x');
    expect(saveChanges()).not.toBeNull();

    await userEvent.clear(notes);           // back to the loaded value
    expect(saveChanges()).toBeNull();

    await userEvent.type(notes, 'rush');
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(saveChanges()).toBeNull();
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ notes: 'rush' });
  });

  it.each([
    ['price', () => userEvent.type(screen.getByLabelText('Price'), '12')],
    ['a link label', () => userEvent.type(screen.getByDisplayValue('Spec'), '!')],
    ['a part unit cost', () => userEvent.type(screen.getByLabelText('Unit cost'), '5')],
    ['a part allocated flag', () => userEvent.click(screen.getByRole('checkbox', { name: 'Allocated' }))],
    ['adding a link', () => userEvent.click(screen.getByRole('button', { name: '+ Add link' }))],
  ])('treats a change to %s as unsaved', async (_label, change) => {
    open('/projects/7/edit');
    await loaded();
    await change();
    expect(saveChanges()).not.toBeNull();
  });

  it('always offers Save on a new project once it has a name', async () => {
    open('/projects/new');
    await screen.findByTitle('Add Bracket.stl');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    await userEvent.type(screen.getByLabelText(/Project name/), 'Shelf');
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });
});

describe('ProjectBuilderScreen - loading a linked customer project', () => {
  const customer = (id: number, name: string) => ({
    id, name, email: `${id}@example.test`, enabled: true, created_at: '', phone: null, company: null, notes: null,
    has_password: false, project_count: 1, active_project_count: 1, outstanding: 0, last_project_at: null,
  });
  // Two accounts with the same name: switching between them must still count as a change.
  const CUSTOMERS = [customer(3, 'Acme Ltd'), customer(4, 'Acme Ltd')];
  const LINKED = {
    ...EXISTING, order_type: 'customer', customer: 'typed before linking', customer_id: 3, customer_name: 'Acme Ltd',
    on_hold: true, due_date: '2026-12-01', price: 40,
  };
  const openLinked = () => open('/projects/7/edit', {
    'GET /api/v1/projects/7': LINKED, 'PATCH /api/v1/projects/7': LINKED, 'GET /api/v1/customers': CUSTOMERS,
  });
  const customerSelect = () => screen.getByRole('combobox', { name: 'Customer' }) as HTMLSelectElement;
  const typeSelect = () => screen.getByDisplayValue(/^(Internal|Customer)$/) as HTMLSelectElement;
  async function loadedLinked() {
    await loaded();
    await screen.findByRole('option', { name: 'Choose a customer…' });
    await waitFor(() => expect(screen.getAllByRole('option', { name: 'Acme Ltd' })).toHaveLength(2));
  }

  it('shows every loaded header field and nothing to save', async () => {
    openLinked();
    await loadedLinked();
    expect(typeSelect().value).toBe('customer');
    expect(customerSelect().value).toBe('3');
    expect(screen.getByRole('checkbox', { name: /On hold/ })).toBeChecked();
    expect(screen.getByDisplayValue('2026-12-01')).toHaveAttribute('type', 'date');
    expect(screen.getByLabelText('Price')).toHaveValue(40);
    expect(saveChanges()).toBeNull();
  });

  it.each([
    ['on hold', () => userEvent.click(screen.getByRole('checkbox', { name: /On hold/ }))],
    ['due date', () => userEvent.clear(screen.getByDisplayValue('2026-12-01'))],
  ])('treats a change to %s as unsaved', async (_label, change) => {
    openLinked();
    await loadedLinked();
    await change();
    expect(saveChanges()).not.toBeNull();
  });

  it('treats switching to another account with the same name as a change, and saves the new account', async () => {
    const api = openLinked();
    await loadedLinked();
    await userEvent.selectOptions(customerSelect(), '4');
    expect(saveChanges()).not.toBeNull();
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ customer_id: 4, customer: 'Acme Ltd', order_type: 'customer' });
  });

  it('treats unlinking to "Other" as a change even with the same name typed back', async () => {
    const api = openLinked();
    await loadedLinked();
    await userEvent.selectOptions(customerSelect(), 'Other (no account)');
    await userEvent.type(screen.getByRole('textbox', { name: 'Customer name' }), 'Acme Ltd');
    expect(saveChanges()).not.toBeNull();
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ customer_id: null, customer: 'Acme Ltd' });
  });

  it('is unsaved while switched to Internal, and clean again once switched back', async () => {
    openLinked();
    await loadedLinked();
    await userEvent.selectOptions(typeSelect(), 'internal');
    expect(saveChanges()).not.toBeNull();
    await userEvent.selectOptions(typeSelect(), 'customer');
    expect(saveChanges()).toBeNull();
  });
});

describe('ProjectBuilderScreen - undo removal', () => {
  it('puts a removed part item back at its original position', async () => {
    open('/projects/7/edit');
    await loaded();
    expect(itemRows()).toEqual(['Bracket.stl', 'Widget.stl']);

    await userEvent.click(screen.getAllByTitle('Remove')[0]);
    expect(itemRows()).toEqual(['Widget.stl']);
    expect(screen.getByText('Removed Bracket.stl')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(itemRows()).toEqual(['Bracket.stl', 'Widget.stl']);
    expect(screen.queryByText(/^Removed /)).toBeNull();
    expect(saveChanges()).toBeNull();       // restored draft equals the loaded one
  });

  it('deletes a removed saved link on save, but not once the removal is undone', async () => {
    const api = open('/projects/7/edit');
    await loaded();
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed Spec')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByDisplayValue('https://example.test/spec')).toBeInTheDocument();
    expect(saveChanges()).toBeNull();

    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'n');
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(writes(api)).not.toContain('DELETE /api/v1/projects/7/links/70');
    expect(writes(api)).toContain('PUT /api/v1/projects/7/links/70');

    await userEvent.click(screen.getByTitle('Remove link'));
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(writes(api).filter(w => w === 'DELETE /api/v1/projects/7/links/70')).toHaveLength(1);
  });

  it('deletes a removed saved non-printed part on save, but not once the removal is undone', async () => {
    const api = open('/projects/7/edit');
    await loaded();
    await userEvent.click(screen.getByTitle('Remove part'));
    expect(screen.getByText('Removed 3mm magnet')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByDisplayValue('3mm magnet')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'n');
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(writes(api)).not.toContain('DELETE /api/v1/projects/7/parts/80');

    await userEvent.click(screen.getByTitle('Remove part'));
    await userEvent.click(saveChanges()!);
    await screen.findByText('Saved');
    expect(writes(api)).toContain('DELETE /api/v1/projects/7/parts/80');
  });

  it('labels an unnamed link by its URL and a blank one generically', async () => {
    open('/projects/new');
    await screen.findByTitle('Add Bracket.stl');
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.type(screen.getByPlaceholderText('https://...'), 'https://a.test');
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed https://a.test')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed link')).toBeInTheDocument();
  });
});

describe('ProjectBuilderScreen - STL folder tree', () => {
  const TREE_FILES = [
    file(1, 'Root.stl', ''),
    file(2, 'Car.stl', '/Toys/Cars'),
    file(3, 'Ball.stl', 'Toys'),
    file(4, 'Gone.stl', 'Toys', { missing: true }),
    file(5, 'Plate.3mf', 'Toys'),
    file(6, 'Deep.stl', 'a/x/a'),
    file(7, 'Slash.stl', 'Tools/'),
    file(8, 'LOUD.STL', 'Toys'),
  ];
  const tree = () => screen.getByText('STL Files').parentElement!;
  const folder = (name: string) => within(tree()).queryAllByRole('button').filter(b => b.textContent === name);

  it('lists every pickable STL at the root, and only a folder\'s own files once it is selected', async () => {
    open('/projects/new', { 'GET /api/v1/files': TREE_FILES });
    await screen.findByTitle('Add Root.stl');
    expect(pickerFiles().sort()).toEqual(['Ball.stl', 'Car.stl', 'Deep.stl', 'LOUD.STL', 'Root.stl', 'Slash.stl']);

    await userEvent.click(folder('Toys')[0]);
    expect(pickerFiles().sort()).toEqual(['Ball.stl', 'LOUD.STL']);

    await userEvent.click(folder('Cars')[0]);
    expect(pickerFiles()).toEqual(['Car.stl']);

    await userEvent.click(folder('All files')[0]);
    expect(pickerFiles()).toHaveLength(6);
  });

  it('selects a nested folder that repeats an ancestor\'s name by its own path', async () => {
    open('/projects/new', { 'GET /api/v1/files': TREE_FILES });
    await screen.findByTitle('Add Root.stl');
    await userEvent.click(folder('a')[0]);
    await userEvent.click(folder('x')[0]);
    const [, nested] = folder('a');
    await userEvent.click(nested);
    expect(pickerFiles()).toEqual(['Deep.stl']);
  });

  it('shows a file stored with a trailing slash in its folder', async () => {
    open('/projects/new', { 'GET /api/v1/files': TREE_FILES });
    await screen.findByTitle('Add Root.stl');
    await userEvent.click(folder('Tools')[0]);
    expect(pickerFiles()).toEqual(['Slash.stl']);
  });

  it('hides the tree while searching and says when nothing matches', async () => {
    open('/projects/new', { 'GET /api/v1/files': TREE_FILES });
    await screen.findByTitle('Add Root.stl');
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'zzz');
    expect(folder('All files')).toHaveLength(0);
    expect(screen.getByText('No matching STL files')).toBeInTheDocument();
  });
});
