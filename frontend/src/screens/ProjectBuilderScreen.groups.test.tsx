import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectBuilderScreen } from './ProjectBuilderScreen';
import { stubFetch } from '../test/fetchStub';

const file = (id: number, name: string) => ({ id, original_filename: name, relative_path: name, folder: '', size_bytes: 1, plate_count: 1,
  uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] });
const FILES = [file(1, 'Bracket.stl'), file(2, 'Widget.stl'), file(3, 'Kit.3mf')];
const group = (id: number, name: string) => ({
  id, project_id: 7, name, sort_order: id, is_default: id === 1, strategy: 'fixed', envelope_x: null, envelope_y: null,
  mix_colours: false, targets: [], max_parts: null, pool: null, pool_warning: null,
});
const GROUPS = [group(1, 'Default Group'), group(2, 'Fine'), group(4, 'Draft')];
const item = (id: number, fileId: number, name: string, groupId: number, over: object = {}) => ({
  id, project_id: 7, file_id: fileId, file_name: name, quantity: 1, quantity_completed: 0, quantity_failed: 0,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: id, group_id: groupId,
  kind: 'stl', source_ref: null, pack_rule: 'split', ...over,
});
const PROJECT = {
  id: 7, name: 'Kit', customer: '', order_type: 'internal', on_hold: false, due_date: null, notes: null, amount_paid: null,
  payment_status: 'unpaid', stage: 'planning', customer_id: null, links: [], parts: [],
  items: [item(50, 1, 'Bracket.stl', 1), item(51, 2, 'Widget.stl', 1),
          item(52, 3, 'Kit.3mf', 2, { kind: '3mf_plate', source_ref: '1', pack_rule: 'together' })],
};

function open() {
  const groups = [...GROUPS];
  const api = stubFetch({
    'GET /api/v1/files': FILES,
    'GET /api/v1/plugins': { plugins: [], selections: {} },
    'GET /api/v1/printers': [],
    'GET /api/v1/projects/7': PROJECT,
    'GET /api/v1/projects/7/groups': () => groups,
    'GET /api/v1/files/3/objects': [],
    'POST /api/v1/projects/7/groups': () => { groups.push(group(3, 'Group 2')); return group(3, 'Group 2'); },
    'PUT /api/v1/projects/7/groups/order': (c: { body: unknown }) =>
      (c.body as { group_ids: number[] }).group_ids.map(id => GROUPS.find(g => g.id === id)!),
    'PATCH /api/v1/projects/7': PROJECT,
    'PUT /api/v1/projects/7/items/50': {}, 'PUT /api/v1/projects/7/items/51': {}, 'PUT /api/v1/projects/7/items/52': {},
    'DELETE /api/v1/projects/7/items/50': {}, 'DELETE /api/v1/projects/7/items/51': {},
    'POST /api/v1/projects/7/items': { id: 60 },
  });
  render(
    <MemoryRouter initialEntries={['/projects/7/edit']}>
      <Routes><Route path="/projects/:id/edit" element={<ProjectBuilderScreen />} /></Routes>
    </MemoryRouter>,
  );
  return api;
}
/** A DataTransfer stand-in that carries data between dragStart and drop (jsdom has none). */
function dataTransfer() {
  const data: Record<string, string> = {};
  return { setData: (k: string, v: string) => { data[k] = v; }, getData: (k: string) => data[k] ?? '', get types() { return Object.keys(data); } };
}
function drag(from: HTMLElement, to: HTMLElement) {
  const dt = dataTransfer();
  fireEvent.dragStart(from, { dataTransfer: dt });
  fireEvent.dragOver(to, { dataTransfer: dt });
  fireEvent.drop(to, { dataTransfer: dt });
}
const rowOf = (name: string) => screen.getByText(name, { selector: 'span[title]' }).closest('[draggable="true"]') as HTMLElement;
const saveAndPut = async (api: ReturnType<typeof open>, id: number) => {
  await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(api.to('PUT', `/api/v1/projects/7/items/${id}`)).toHaveLength(1));
  return api.to('PUT', `/api/v1/projects/7/items/${id}`)[0].body as { group_id: number; quantity: number; pack_rule: string };
};
const ready = () => screen.findByText('Kit.3mf', { selector: 'span[title]' });

afterEach(() => vi.unstubAllGlobals());

describe('Builder group banner (BIZ-363)', () => {
  it('shows each group with its items and parts, filters by the selected badge, and colours rows', async () => {
    open();
    await ready();
    expect(screen.getByTestId('badge-1').textContent).toContain('2 items · 2 parts');
    expect(screen.getByTestId('badge-2').textContent).toContain('1 item · 1 part');
    expect(screen.getAllByTitle('Remove')).toHaveLength(3);                                   // All
    await userEvent.click(screen.getByTestId('badge-2'));
    expect(screen.getAllByTitle('Remove')).toHaveLength(1);
    expect(screen.getByText('Kit.3mf', { selector: 'span[title]' })).toBeTruthy();
    await userEvent.click(screen.getByRole('tab', { name: /^All/ }));
    expect(screen.getAllByTitle('Remove')).toHaveLength(3);
  });

  it('moves a dragged row to the badge it is dropped on, and saves its group', async () => {
    const api = open();
    await ready();
    drag(rowOf('Bracket.stl'), screen.getByTestId('badge-2'));
    expect(screen.getByTestId('badge-2').textContent).toContain('2 items');
    expect((await saveAndPut(api, 50)).group_id).toBe(2);
  });

  it('dropping on "+ New group" creates a group and moves the rows there', async () => {
    const api = open();
    await ready();
    drag(rowOf('Widget.stl'), screen.getByTestId('badge-new'));
    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/groups')).toHaveLength(1));
    expect((api.to('POST', '/api/v1/projects/7/groups')[0].body as { name: string }).name).toMatch(/^Group \d+$/);
    expect((await saveAndPut(api, 51)).group_id).toBe(3);
  });

  it('a new row joins the badge being shown', async () => {
    const api = open();
    await ready();
    await userEvent.click(screen.getByTestId('badge-2'));
    await userEvent.click(screen.getByTitle('Add Widget.stl'));
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/items')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/projects/7/items')[0].body).toMatchObject({ file_id: 2, group_id: 2 });
  });
});

describe('Builder item sections and bulk edit (BIZ-363)', () => {
  it('groups rows per source file (loose STLs together), collapses with group dots, and re-sorts By group', async () => {
    open();
    await ready();
    expect(within(screen.getByTestId('section-stl')).getByText('Loose STL files')).toBeTruthy();
    expect(screen.getByTestId('section-f:3')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Collapse all' }));
    expect(screen.queryAllByTitle('Remove')).toHaveLength(0);
    expect(within(screen.getByTestId('section-stl')).getByLabelText('Groups of its items').children).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: 'Expand all' }));
    await userEvent.click(screen.getByRole('button', { name: 'By group' }));
    expect(screen.getByTestId('section-g:1')).toBeTruthy();
    expect(screen.getByTestId('section-g:2')).toBeTruthy();
  });

  it('applies qty, packing and group to the checked rows only, and removes them together', async () => {
    const api = open();
    await ready();
    await userEvent.click(screen.getByLabelText('Select Bracket.stl'));
    await userEvent.click(screen.getByLabelText('Select Kit.3mf'));
    expect(screen.getByTestId('bulk-bar').textContent).toContain('2 selected');
    await userEvent.type(screen.getByLabelText('Quantity for selected'), '7');
    await userEvent.click(screen.getByRole('button', { name: 'Set qty' }));
    expect(screen.getByLabelText('Quantity of Bracket.stl')).toHaveValue(7);
    expect(screen.getByLabelText('Quantity of Widget.stl')).toHaveValue(1);
    await userEvent.selectOptions(screen.getByLabelText('Set packing rule'), 'solo');      // allowed for STLs and plates
    await userEvent.selectOptions(screen.getByLabelText('Move selected to group'), '2');
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/items/52')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/projects/7/items/50')[0].body).toMatchObject({ quantity: 7, pack_rule: 'solo', group_id: 2 });
    expect(api.to('PUT', '/api/v1/projects/7/items/52')[0].body).toMatchObject({ quantity: 7, pack_rule: 'solo', group_id: 2 });
    expect(api.to('PUT', '/api/v1/projects/7/items/51')[0].body).toMatchObject({ quantity: 1, pack_rule: 'split', group_id: 1 });
  });

  it('a section checkbox selects its rows, and shows mixed when only some are', async () => {
    open();
    await ready();
    const box = screen.getByLabelText('Select all in Loose STL files') as HTMLInputElement;
    await userEvent.click(screen.getByLabelText('Select Bracket.stl'));
    expect(box.indeterminate).toBe(true);
    await userEvent.click(box);
    expect(screen.getByTestId('bulk-bar').textContent).toContain('2 selected');
    expect(box.checked).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Clear selection' }));
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
  });

  it('only offers packing rules a selected row can use', async () => {
    open();
    await ready();
    await userEvent.click(screen.getByLabelText('Select Bracket.stl'));
    const options = Array.from((screen.getByLabelText('Set packing rule') as HTMLSelectElement).options).map(o => o.textContent);
    expect(options).toEqual(['Packing…', 'Split', 'Solo']);                                  // an STL can't be Together/Locked
  });

  it('the select-all box takes only the rows the filter shows, and Remove deletes them on save', async () => {
    const api = open();
    await ready();
    await userEvent.click(screen.getByTestId('badge-1'));
    await userEvent.click(screen.getByLabelText('Select every shown item'));
    expect(screen.getByTestId('bulk-bar').textContent).toContain('2 selected');
    await userEvent.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Remove' }));
    expect(screen.getByText(/Removed 2 items/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/projects/7/items/51')).toHaveLength(1));
    expect(api.to('DELETE', '/api/v1/projects/7/items/50')).toHaveLength(1);
    expect(api.calls.some(c => c.method === 'DELETE' && c.url.endsWith('/items/52'))).toBe(false);
  });

  it('sets one filament on every selected row', async () => {
    open();
    await ready();
    await userEvent.click(screen.getByLabelText('Select Bracket.stl'));
    await userEvent.click(screen.getByLabelText('Select Widget.stl'));
    await userEvent.click(screen.getByRole('button', { name: 'Set filament…' }));
    const editor = screen.getByTestId('bulk-filament');
    await userEvent.selectOptions(within(editor).getByLabelText('Filament type'), 'PETG');
    await userEvent.click(within(editor).getByRole('button', { name: 'Apply' }));
    const types = (screen.getAllByLabelText('Filament type') as HTMLSelectElement[]).map(s => s.value);
    expect(types).toEqual(['PETG', 'PETG', '']);                                             // Kit.3mf untouched
  });
});

describe('Builder Groups tab (BIZ-363)', () => {
  it('hides All, edits the selected group, and dragging badges sets queue order', async () => {
    const api = open();
    await ready();
    await userEvent.click(screen.getByRole('tab', { name: /^Groups/ }));
    expect(screen.queryByRole('tab', { name: /^All/ })).toBeNull();
    expect(await screen.findByDisplayValue('Default Group')).toBeTruthy();                  // the Default Group is selected
    await userEvent.click(screen.getByTestId('badge-2'));
    expect(await screen.findByDisplayValue('Fine')).toBeTruthy();
    drag(screen.getByTestId('badge-2'), screen.getByTestId('badge-1'));
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/groups/order')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/projects/7/groups/order')[0].body).toEqual({ group_ids: [2, 1, 4] });
  });

  it('a badge dragged onto a later one lands after it (so it can move to the end)', async () => {
    const api = open();
    await ready();
    await userEvent.click(screen.getByRole('tab', { name: /^Groups/ }));
    await screen.findByDisplayValue('Default Group');
    drag(screen.getByTestId('badge-1'), screen.getByTestId('badge-4'));
    await waitFor(() => expect(api.to('PUT', '/api/v1/projects/7/groups/order')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/projects/7/groups/order')[0].body).toEqual({ group_ids: [2, 4, 1] });
  });
});
