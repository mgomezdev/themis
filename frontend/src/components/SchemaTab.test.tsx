import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SchemaTab } from './SchemaTab';
import { Reply, stubFetch } from '../test/fetchStub';
import type { TabSchema } from '../api/plugins';

const SCHEMA: TabSchema = {
  title: 'Library',
  blocks: [
    { type: 'form', title: 'Defaults', load: 'defaults', save: 'defaults', fields: [
      { key: 'currency', label: 'Currency', type: 'text', required: true },
      { key: 'spool_g', label: 'Spool size (g)', type: 'number' },
      { key: 'archive_empty', label: 'Archive empty spools', type: 'checkbox' },
      { key: 'unit', label: 'Unit', type: 'select', options: [{ value: 'g', label: 'grams' }, { value: 'kg', label: 'kilograms' }] },
    ] },
    { type: 'table', title: 'Materials', data: 'materials', empty: 'No materials yet.',
      columns: [{ key: 'name', label: 'Name' }, { key: 'material', label: 'Type' }],
      create: { path: 'materials', label: 'Add material', fields: [{ key: 'name', label: 'Name', type: 'text', required: true }, { key: 'material', label: 'Type', type: 'text' }] },
      row_actions: [{ label: 'Archive', method: 'POST', path: 'materials/{id}/archive', confirm: 'Archive it?' },
                    { label: 'Delete', method: 'DELETE', path: 'materials/{id}' }] },
  ],
};

const P = '/api/v1/plugins/demo';
const routes = (over: Record<string, unknown> = {}) => ({
  [`GET ${P}/ui/library`]: SCHEMA,
  [`GET ${P}/defaults`]: { currency: 'EUR', spool_g: 1000, archive_empty: true, unit: 'g' },
  [`GET ${P}/materials`]: [{ id: 7, name: 'PLA Red', material: 'PLA' }, { id: 8, name: 'PETG', material: null }],
  ...over,
});

describe('SchemaTab', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('renders the title and both block kinds from the plugin-served schema', async () => {
    stubFetch(routes());
    render(<SchemaTab pluginId="demo" tabId="library" />);

    expect(await screen.findByRole('heading', { name: 'Library' })).toBeTruthy();
    expect((await screen.findByLabelText('Currency') as HTMLInputElement).value).toBe('EUR');
    expect((screen.getByLabelText('Spool size (g)') as HTMLInputElement).value).toBe('1000');
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('g');
    expect(screen.getByText('Currency *')).toBeTruthy();
    const table = within(await screen.findByTestId('schema-table'));
    expect(table.getByText('PLA Red')).toBeTruthy();
    expect(table.getAllByRole('row')).toHaveLength(3);                                    // header + 2 rows
  });

  it('saves a form block with typed values (numbers as numbers, blanks as null, checkbox as boolean)', async () => {
    const api = stubFetch(routes({ [`PUT ${P}/defaults`]: { ok: true } }));
    render(<SchemaTab pluginId="demo" tabId="library" />);
    const size = await screen.findByLabelText('Spool size (g)');

    await userEvent.clear(size);
    await userEvent.type(size, '750');
    await userEvent.selectOptions(screen.getByLabelText('Unit'), 'kg');
    await userEvent.click(within(screen.getByTestId('schema-form')).getByRole('switch'));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await screen.findByRole('status');
    expect(api.to('PUT', `${P}/defaults`)[0].body).toEqual({ currency: 'EUR', spool_g: 750, archive_empty: false, unit: 'kg' });
  });

  it('creates a row, then reloads the table; the add button waits for the required fields', async () => {
    let rows = [{ id: 7, name: 'PLA Red', material: 'PLA' }];
    const api = stubFetch(routes({
      [`GET ${P}/materials`]: () => rows,
      [`POST ${P}/materials`]: (c: { body: { name: string; material: string | null } }) => { rows = [...rows, { id: 9, ...c.body } as never]; return { id: 9 }; },
    }));
    render(<SchemaTab pluginId="demo" tabId="library" />);
    await screen.findByText('PLA Red');
    const add = screen.getByRole('button', { name: 'Add material' }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);

    await userEvent.type(within(screen.getByTestId('schema-table')).getAllByLabelText('Name')[0], 'ABS Grey');
    expect(add.disabled).toBe(false);
    await userEvent.click(add);

    expect(await screen.findByText('ABS Grey')).toBeTruthy();
    expect(api.to('POST', `${P}/materials`)[0].body).toEqual({ name: 'ABS Grey', material: null });
  });

  it('runs a row action against the row (id substituted into the path), asking first when the schema says so', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true);
    const api = stubFetch(routes({ [`POST ${P}/materials/7/archive`]: { ok: true }, [`DELETE ${P}/materials/8`]: { ok: true } }));
    render(<SchemaTab pluginId="demo" tabId="library" />);
    const redRow = within((await screen.findByText('PLA Red')).closest('tr') as HTMLElement);

    await userEvent.click(redRow.getByRole('button', { name: 'Archive' }));          // declined
    expect(api.to('POST', `${P}/materials/7/archive`)).toEqual([]);
    await userEvent.click(redRow.getByRole('button', { name: 'Archive' }));          // confirmed
    await waitFor(() => expect(api.to('POST', `${P}/materials/7/archive`)).toHaveLength(1));
    expect(confirm).toHaveBeenCalledWith('Archive it?');

    await userEvent.click(within((screen.getByText('PETG')).closest('tr') as HTMLElement).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(api.to('DELETE', `${P}/materials/8`)).toHaveLength(1));        // no confirm in the schema: no prompt
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it('shows the empty text, and a load or action failure, instead of failing silently', async () => {
    stubFetch(routes({ [`GET ${P}/materials`]: [], [`GET ${P}/defaults`]: new Reply(500, { detail: 'defaults unavailable' }) }));
    render(<SchemaTab pluginId="demo" tabId="library" />);
    expect(await screen.findByText('No materials yet.')).toBeTruthy();
    expect(await screen.findByText('defaults unavailable')).toBeTruthy();
  });

  it('shows why when the schema itself cannot be loaded', async () => {
    stubFetch({ [`GET ${P}/ui/library`]: new Reply(404, { detail: "'demo' has no schema tab 'library'" }) });
    render(<SchemaTab pluginId="demo" tabId="library" />);
    expect((await screen.findByRole('alert')).textContent).toBe("'demo' has no schema tab 'library'");
  });
});
