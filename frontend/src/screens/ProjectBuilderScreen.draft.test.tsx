// Characterization tests for the Builder's local draft state: dirty tracking, undo of removals, repeat saves,
// and the model folder tree. Written before the ProjectBuilderScreen refactor (docs/superpowers/plans/
// 2026-11-11-project-builder-refactor.md) so every step of it is held to the same screen behavior.
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const file = (id: number, name: string, folder = '', extra: object = {}) => ({
  id, original_filename: name, relative_path: name, folder, size_bytes: 1, plate_count: 1, uploaded_at: '',
  missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [], kind: 'stl', ...extra,
});
const FILES = [file(1, 'Bracket.stl'), file(2, 'Widget.stl')];
const item = (id: number, file_id: number, file_name: string, extra: object = {}) => ({
  id, project_id: 7, file_id, file_name, quantity: 1, quantity_completed: 0, quantity_failed: 0,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0,
  kind: 'stl', source_ref: null, pack_rule: 'split', group_id: null, ...extra,
});

const EXISTING = {
  id: 7, name: 'Gridfinity', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null,
  amount_paid: null, payment_status: 'unpaid', stage: 'planning', customer_id: null, customer_name: null, price: null,
  links: [{ id: 70, project_id: 7, url: 'https://example.test/spec', label: 'Spec', sort_order: 0, created_at: '' }],
  parts: [{ id: 80, project_id: 7, name: '3mm magnet', quantity: 4, allocated: false, unit_cost: 0.25, sort_order: 0, created_at: '' }],
  items: [
    item(50, 1, 'Bracket.stl', { quantity: 2, filament_type: 'PLA', filament_subtype: 'any' }),
    item(51, 2, 'Widget.stl', { sort_order: 1 }),
  ],
};

function open(path: string, over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/files': FILES,
    'GET /api/v1/plugins': { plugins: [], selections: {} },
    'GET /api/v1/printers': [],
    'GET /api/v1/customers': [],
    'GET /api/v1/projects/7': EXISTING,
    'GET /api/v1/projects/7/groups': [],
    'PATCH /api/v1/projects/7': EXISTING,
    'PUT /api/v1/projects/7/items/50': EXISTING.items[0],
    'PUT /api/v1/projects/7/items/51': EXISTING.items[1],
    'DELETE /api/v1/projects/7/items/50': { deleted: 50 },
    'DELETE /api/v1/projects/7/items/51': { deleted: 51 },
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
const toTab = (name: RegExp) => userEvent.click(screen.getByRole('tab', { name }));
const saveBtn = () => screen.getByRole('button', { name: /^(Save draft|Saving…)$/ }) as HTMLButtonElement;
const dirty = () => !saveBtn().disabled;
async function save() {
  await userEvent.click(saveBtn());
  await screen.findByText('Saved');
}
/** File names of the project's item rows. */
const itemRows = () => screen.queryAllByText(/\.(stl|3mf|gcode)$/i, { selector: 'span[title]' }).map(e => e.textContent);
/** Label of every file button in the model picker, in order. */
const pickerFiles = () => screen.queryAllByTitle(/^Add /).map(e => e.getAttribute('title')!.slice(4));
const rowOf = (fileName: string) => screen.getByLabelText(`Quantity of ${fileName}`).parentElement!;

/** An existing project, loaded, on its Details tab. */
async function loadedDetails() {
  await screen.findByText('Bracket.stl', { selector: 'span[title]' });
  await toTab(/^Details/);
  await screen.findByDisplayValue('https://example.test/spec');
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('ProjectBuilderScreen - dirty tracking (edit mode)', () => {
  it('enables Save draft only while the draft differs from what was loaded or last saved', async () => {
    const api = open('/projects/7/edit');
    await loadedDetails();
    expect(dirty()).toBe(false);

    const notes = screen.getByPlaceholderText('Optional notes');
    await userEvent.type(notes, 'x');
    expect(dirty()).toBe(true);

    await userEvent.clear(notes);           // back to the loaded value
    expect(dirty()).toBe(false);

    await userEvent.type(notes, 'rush');
    await save();
    expect(dirty()).toBe(false);
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ notes: 'rush' });
  });

  it('hides the Saved toast after 2 seconds', async () => {
    open('/projects/7/edit');
    await loadedDetails();
    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'x');
    const timeouts = vi.spyOn(window, 'setTimeout');
    await save();
    const call = timeouts.mock.calls.find(c => c[1] === 2000)!;
    timeouts.mockRestore();
    act(() => (call[0] as () => void)());
    expect(screen.queryByText('Saved')).toBeNull();
  });

  it.each([
    ['price', () => userEvent.type(screen.getByLabelText('Price'), '12')],
    ['a link label', () => userEvent.type(screen.getByDisplayValue('Spec'), '!')],
    ['a part unit cost', () => userEvent.type(screen.getByLabelText('Unit cost'), '5')],
    ['a part allocated flag', () => userEvent.click(screen.getByRole('checkbox', { name: 'Allocated' }))],
    ['adding a link', () => userEvent.click(screen.getByRole('button', { name: '+ Add link' }))],
  ])('treats a change to %s as unsaved', async (_label, change) => {
    open('/projects/7/edit');
    await loadedDetails();
    await change();
    expect(dirty()).toBe(true);
  });

  it.each([
    ['an item quantity', async () => {
      const qty = screen.getByLabelText('Quantity of Widget.stl');
      await userEvent.clear(qty);
      await userEvent.type(qty, '3');
    }],
    ['an added item', () => userEvent.click(screen.getByTitle('Add Bracket.stl'))],
  ])('treats %s as unsaved', async (_label, change) => {
    open('/projects/7/edit');
    await screen.findByText('Bracket.stl', { selector: 'span[title]' });
    expect(dirty()).toBe(false);
    await change();
    expect(dirty()).toBe(true);
  });

  it('stays unsaved after removing a saved item and re-adding the same file (a delete is pending)', async () => {
    const api = open('/projects/7/edit', { 'POST /api/v1/projects/7/items': { id: 60 } });
    await screen.findByText('Bracket.stl', { selector: 'span[title]' });
    await userEvent.click(within(rowOf('Widget.stl')).getByTitle('Remove'));
    await userEvent.click(screen.getByTitle('Add Widget.stl'));
    expect(itemRows().sort()).toEqual(['Bracket.stl', 'Widget.stl']);
    expect(dirty()).toBe(true);
    await save();
    expect(api.to('DELETE', '/api/v1/projects/7/items/51')).toHaveLength(1);
    expect(api.to('POST', '/api/v1/projects/7/items')).toHaveLength(1);
  });

  it('can save a new project once it has a name', async () => {
    open('/projects/new');
    await screen.findByLabelText(/Project name/);
    expect(saveBtn()).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Project name/), 'Shelf');
    expect(saveBtn()).not.toBeDisabled();
  });
});

describe('ProjectBuilderScreen - undo removal', () => {
  it('puts a removed item back, and the restored draft is clean', async () => {
    open('/projects/7/edit');
    await screen.findByText('Bracket.stl', { selector: 'span[title]' });
    expect(itemRows().sort()).toEqual(['Bracket.stl', 'Widget.stl']);

    await userEvent.click(within(rowOf('Bracket.stl')).getByTitle('Remove'));
    expect(itemRows()).toEqual(['Widget.stl']);
    expect(screen.getByText('Removed Bracket.stl')).toBeInTheDocument();
    expect(dirty()).toBe(true);

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(itemRows().sort()).toEqual(['Bracket.stl', 'Widget.stl']);
    expect(screen.queryByText(/^Removed /)).toBeNull();
    expect(dirty()).toBe(false);
  });

  it('deletes a removed saved link on save, but not once the removal is undone', async () => {
    const api = open('/projects/7/edit');
    await loadedDetails();
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed Spec')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByDisplayValue('https://example.test/spec')).toBeInTheDocument();
    expect(dirty()).toBe(false);

    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'n');
    await save();
    expect(writes(api)).not.toContain('DELETE /api/v1/projects/7/links/70');
    expect(writes(api)).toContain('PUT /api/v1/projects/7/links/70');

    await userEvent.click(screen.getByTitle('Remove link'));
    await save();
    expect(writes(api).filter(w => w === 'DELETE /api/v1/projects/7/links/70')).toHaveLength(1);
  });

  it('deletes a removed saved non-printed part on save, but not once the removal is undone', async () => {
    const api = open('/projects/7/edit');
    await loadedDetails();
    await userEvent.click(screen.getByTitle('Remove part'));
    expect(screen.getByText('Removed 3mm magnet')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByDisplayValue('3mm magnet')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText('Optional notes'), 'n');
    await save();
    expect(writes(api)).not.toContain('DELETE /api/v1/projects/7/parts/80');

    await userEvent.click(screen.getByTitle('Remove part'));
    await save();
    expect(writes(api)).toContain('DELETE /api/v1/projects/7/parts/80');
  });

  it('labels an unnamed link by its URL and a blank one generically', async () => {
    open('/projects/new');
    await screen.findByLabelText(/Project name/);
    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.type(screen.getByPlaceholderText('https://...'), 'https://a.test');
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed https://a.test')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '+ Add link' }));
    await userEvent.click(screen.getByTitle('Remove link'));
    expect(screen.getByText('Removed link')).toBeInTheDocument();
  });
});

describe('ProjectBuilderScreen - after a save', () => {
  it('retries a failed save without repeating the deletes that already went through', async () => {
    let linkPuts = 0;
    const api = open('/projects/7/edit', {
      'PUT /api/v1/projects/7/links/70': () => (++linkPuts === 1 ? new Reply(500, 'disk full') : EXISTING.links[0]),
    });
    await loadedDetails();
    await userEvent.click(screen.getByTitle('Remove part'));
    await toTab(/^Items/);
    await userEvent.click(within(rowOf('Widget.stl')).getByTitle('Remove'));
    await userEvent.click(saveBtn());
    await screen.findByText(/Save failed/);

    await userEvent.click(saveBtn());
    await screen.findByText('Saved');
    expect(api.to('DELETE', '/api/v1/projects/7/items/51')).toHaveLength(1);
    expect(api.to('DELETE', '/api/v1/projects/7/parts/80')).toHaveLength(1);
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
    await loadedDetails();
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
    expect(dirty()).toBe(false);
  });

  it.each([
    ['on hold', () => userEvent.click(screen.getByRole('checkbox', { name: /On hold/ }))],
    ['due date', () => userEvent.click(screen.getByTitle('Clear due date'))],
  ])('treats a change to %s as unsaved', async (_label, change) => {
    openLinked();
    await loadedLinked();
    await change();
    expect(dirty()).toBe(true);
  });

  it('treats switching to another account with the same name as a change, and saves the new account', async () => {
    const api = openLinked();
    await loadedLinked();
    await userEvent.selectOptions(customerSelect(), '4');
    expect(dirty()).toBe(true);
    await save();
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ customer_id: 4, customer: 'Acme Ltd', order_type: 'customer' });
  });

  it('treats unlinking to "Other" as a change even with the same name typed back', async () => {
    const api = openLinked();
    await loadedLinked();
    await userEvent.selectOptions(customerSelect(), 'Other (no account)');
    await userEvent.type(screen.getByRole('textbox', { name: 'Customer name' }), 'Acme Ltd');
    expect(dirty()).toBe(true);
    await save();
    expect(api.to('PATCH', '/api/v1/projects/7')[0].body).toMatchObject({ customer_id: null, customer: 'Acme Ltd' });
  });

  it('is unsaved while switched to Internal, and clean again once switched back', async () => {
    openLinked();
    await loadedLinked();
    await userEvent.selectOptions(typeSelect(), 'internal');
    expect(dirty()).toBe(true);
    await userEvent.selectOptions(typeSelect(), 'customer');
    expect(dirty()).toBe(false);
  });
});

describe('ProjectBuilderScreen - model folder tree', () => {
  const TREE_FILES = [
    file(1, 'Root.stl', ''),
    file(2, 'Car.stl', '/Toys/Cars'),
    file(3, 'Ball.stl', 'Toys'),
    file(4, 'Gone.stl', 'Toys', { missing: true }),
    file(5, 'Plate.3mf', 'Toys', { kind: '3mf' }),
    file(6, 'Deep.stl', 'a/x/a'),
    file(7, 'Slash.stl', 'Tools/'),
    file(8, 'LOUD.STL', 'Toys'),
    file(9, 'notes.txt', 'Toys'),
    file(10, 'Sliced.gcode', 'Toys', { kind: 'gcode' }),
  ];
  const tree = () => screen.getByText('STL Files').parentElement!;
  const folder = (name: string) => within(tree()).queryAllByRole('button').filter(b => b.textContent === name);
  const openTree = async () => {
    open('/projects/new', { 'GET /api/v1/files': TREE_FILES });
    await toTab(/^Items/);
    await screen.findByTitle('Add Root.stl');
  };

  it('lists every pickable model at the root, and only a folder\'s own files once it is selected', async () => {
    await openTree();
    expect(pickerFiles().sort()).toEqual(
      ['Ball.stl', 'Car.stl', 'Deep.stl', 'LOUD.STL', 'Plate.3mf', 'Root.stl', 'Slash.stl', 'Sliced.gcode']);

    await userEvent.click(folder('Toys')[0]);
    expect(pickerFiles().sort()).toEqual(['Ball.stl', 'LOUD.STL', 'Plate.3mf', 'Sliced.gcode']);

    await userEvent.click(folder('Cars')[0]);
    expect(pickerFiles()).toEqual(['Car.stl']);

    await userEvent.click(folder('All files')[0]);
    expect(pickerFiles()).toHaveLength(8);
  });

  it('selects a nested folder that repeats an ancestor\'s name by its own path', async () => {
    await openTree();
    await userEvent.click(folder('a')[0]);
    await userEvent.click(folder('x')[0]);
    const [, nested] = folder('a');
    await userEvent.click(nested);
    expect(pickerFiles()).toEqual(['Deep.stl']);
  });

  it('shows a file stored with a trailing slash in its folder', async () => {
    await openTree();
    await userEvent.click(folder('Tools')[0]);
    expect(pickerFiles()).toEqual(['Slash.stl']);
  });

  it('hides the tree while searching and says when nothing matches', async () => {
    await openTree();
    await userEvent.type(screen.getByPlaceholderText('Search files…'), 'zzz');
    expect(folder('All files')).toHaveLength(0);
    expect(screen.getByText('No matching STL files')).toBeInTheDocument();
  });
});
