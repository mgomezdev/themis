import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilamentLibraryScreen } from './FilamentLibraryScreen';
import { Reply, stubFetch } from '../test/fetchStub';
import { ALL_CAPS, mkMaterial, mkPlugin, mkSpool, pluginsBody } from '../test/inventoryFixtures';
import { resetPluginStore } from '../api/plugins';

const LIB_CAPS = [...ALL_CAPS.filter(c => c !== 'REMOTE'), 'MANAGE_MATERIALS', 'MANAGE_SPOOLS'];
const list = (items: unknown[]) => ({ provider: 'p', stale: false, as_of: 'x', items });

const RED = mkMaterial({ ref: '1', name: 'PLA Red', vendor: 'Acme', material: 'PLA', color_hex: '#FF0000' });
const OLD = mkMaterial({ ref: '2', name: 'Old PETG', material: 'PETG', archived: true });

function boot(caps = LIB_CAPS, over: Record<string, unknown> = {}) {
  const spools = [mkSpool('1', { material_ref: '1', remaining_g: 412.4, initial_g: 1000, location: 'Shelf A', material: { ...RED } })];
  return stubFetch({
    'GET /api/v1/plugins': pluginsBody(mkPlugin({ id: 'p', capabilities: caps })),
    'GET /api/v1/inventory/materials?include_archived=true': list([RED, OLD]),
    'GET /api/v1/inventory/spools?include_archived=true': list(spools),
    ...over,
  });
}

describe('FilamentLibraryScreen', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('lists spools with location and weight, hides archived items until asked', async () => {
    boot();
    render(<FilamentLibraryScreen />);
    const row = within(await screen.findByTestId('spool-1'));
    expect(row.getByText(/#1 Acme PLA Red/)).toBeTruthy();
    expect(row.getByText('Shelf A')).toBeTruthy();
    expect(row.getByText('412 g / 1000 g')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Materials' }));
    expect(screen.getByTestId('material-1')).toBeTruthy();
    expect(screen.queryByTestId('material-2')).toBeNull();                           // archived
    await userEvent.click(screen.getByLabelText('Show archived'));
    expect(within(screen.getByTestId('material-2')).getByText(/archived/)).toBeTruthy();
  });

  it('adds a material with only the fields that were filled in', async () => {
    const api = boot(LIB_CAPS, { 'POST /api/v1/inventory/materials': { ref: '3' } });
    render(<FilamentLibraryScreen />);
    await userEvent.click(await screen.findByRole('button', { name: 'Materials' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add material' }));
    const form = within(screen.getByTestId('material-form'));
    const save = form.getByRole('button', { name: 'Add material' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);                                                  // a name is required

    await userEvent.type(form.getByLabelText('Material name'), 'PETG Blue');
    await userEvent.type(form.getByLabelText('Material type'), 'PETG');
    await userEvent.type(form.getByLabelText('Diameter'), '1.75');
    await userEvent.click(save);

    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/materials')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/inventory/materials')[0].body).toEqual({ name: 'PETG Blue', material: 'PETG', vendor: null, color_hex: null, density: null, diameter: 1.75 });
    await waitFor(() => expect(api.to('GET', '/api/v1/inventory/materials?include_archived=true')).toHaveLength(2));   // reloaded
  });

  it('edits and archives / restores a material', async () => {
    const api = boot(LIB_CAPS, {
      'PATCH /api/v1/inventory/materials/1': { ref: '1' }, 'POST /api/v1/inventory/materials/1/archive': { ref: '1' },
      'POST /api/v1/inventory/materials/2/archive': { ref: '2' },
    });
    render(<FilamentLibraryScreen />);
    await userEvent.click(await screen.findByRole('button', { name: 'Materials' }));

    await userEvent.click(within(screen.getByTestId('material-1')).getByRole('button', { name: 'Edit' }));
    const name = within(screen.getByTestId('material-form')).getByLabelText('Material name');
    await userEvent.clear(name);
    await userEvent.type(name, 'PLA Crimson');
    await userEvent.click(screen.getByRole('button', { name: 'Save material' }));
    await waitFor(() => expect((api.to('PATCH', '/api/v1/inventory/materials/1')[0].body as { name: string }).name).toBe('PLA Crimson'));

    await userEvent.click(within(await screen.findByTestId('material-1')).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/materials/1/archive')[0].body).toEqual({ archived: true }));
    await userEvent.click(screen.getByLabelText('Show archived'));
    await userEvent.click(within(screen.getByTestId('material-2')).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/materials/2/archive')[0].body).toEqual({ archived: false }));
  });

  it('adds a spool for a non-archived material; the weight fields only exist with WRITE_WEIGHT', async () => {
    const api = boot(LIB_CAPS, { 'POST /api/v1/inventory/spools': { ref: '9' } });
    render(<FilamentLibraryScreen />);
    await userEvent.click(await screen.findByRole('button', { name: 'Add spool' }));
    const form = within(screen.getByTestId('spool-form'));
    const options = Array.from((form.getByLabelText('Spool material') as HTMLSelectElement).options).map(o => o.textContent);
    expect(options).toEqual(['Material *', 'Acme PLA Red']);                            // the archived material is not offered
    const save = form.getByRole('button', { name: 'Add spool' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    await userEvent.selectOptions(form.getByLabelText('Spool material'), '1');
    await userEvent.type(form.getByLabelText('Storage location'), 'Drawer 2');
    await userEvent.type(form.getByLabelText('Initial weight'), '1000');
    await userEvent.type(form.getByLabelText('Remaining weight'), '750');
    await userEvent.click(save);

    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/spools')).toHaveLength(1));
    expect(api.to('POST', '/api/v1/inventory/spools')[0].body).toEqual({ material_ref: '1', label: null, location: 'Drawer 2', initial_g: 1000, remaining_g: 750 });
  });

  it('sets a spool weight, edits its location, and archives it', async () => {
    const api = boot(LIB_CAPS, {
      'PUT /api/v1/inventory/spools/1/remaining': { ref: '1' }, 'PATCH /api/v1/inventory/spools/1': { ref: '1' },
      'POST /api/v1/inventory/spools/1/archive': { ref: '1' },
    });
    render(<FilamentLibraryScreen />);
    const row = within(await screen.findByTestId('spool-1'));

    await userEvent.click(row.getByRole('button', { name: 'Set weight' }));
    const w = screen.getByLabelText('New weight for spool 1') as HTMLInputElement;
    expect(w.value).toBe('412');
    await userEvent.clear(w);
    await userEvent.type(w, '380');
    await userEvent.click(screen.getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/inventory/spools/1/remaining')[0].body).toEqual({ remaining_g: 380 }));

    await userEvent.click(within(await screen.findByTestId('spool-1')).getByRole('button', { name: 'Edit' }));
    const loc = within(screen.getByTestId('spool-form')).getByLabelText('Storage location');
    await userEvent.clear(loc);
    await userEvent.type(loc, 'Box 5');
    await userEvent.click(screen.getByRole('button', { name: 'Save spool' }));
    await waitFor(() => expect(api.to('PATCH', '/api/v1/inventory/spools/1')[0].body).toEqual({ label: 'Acme PLA Red', location: 'Box 5' }));

    await userEvent.click(within(await screen.findByTestId('spool-1')).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/inventory/spools/1/archive')[0].body).toEqual({ archived: true }));
  });

  it('shows the provider\'s error and keeps the form open', async () => {
    boot(LIB_CAPS, { 'POST /api/v1/inventory/materials': new Reply(422, { detail: 'a material needs a name' }) });
    render(<FilamentLibraryScreen />);
    await userEvent.click(await screen.findByRole('button', { name: 'Materials' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add material' }));
    await userEvent.type(screen.getByLabelText('Material name'), 'x');
    await userEvent.click(within(screen.getByTestId('material-form')).getByRole('button', { name: 'Add material' }));
    expect((await screen.findByRole('alert')).textContent).toBe('a material needs a name');
    expect(screen.getByTestId('material-form')).toBeTruthy();
  });

  it('capability gating: no weight control without WRITE_WEIGHT; only the sections the provider can manage', async () => {
    boot(['MANAGE_SPOOLS']);
    render(<FilamentLibraryScreen />);
    const row = within(await screen.findByTestId('spool-1'));
    expect(row.queryByRole('button', { name: 'Set weight' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Materials' })).toBeNull();            // cannot manage materials: no such section
    await userEvent.click(screen.getByRole('button', { name: 'Add spool' }));
    expect(screen.queryByLabelText('Remaining weight')).toBeNull();
  });

  it('a materials-only provider opens on Materials (no blank pane)', async () => {
    boot(['MANAGE_MATERIALS']);
    render(<FilamentLibraryScreen />);
    expect(await screen.findByRole('button', { name: 'Add material' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Spools' })).toBeNull();
  });

  it.each([
    ['no provider', null],
    ['a provider whose library lives elsewhere', mkPlugin({ id: 'p', capabilities: ['TRACKS_WEIGHT', 'REMOTE'] })],
  ])('%s: nothing to manage, and the library is not even requested', async (_n, plugin) => {
    const api = stubFetch({ 'GET /api/v1/plugins': pluginsBody(plugin) });
    render(<FilamentLibraryScreen />);
    expect(await screen.findByTestId('library-unavailable')).toBeTruthy();
    expect(api.to('GET', '/api/v1/inventory/spools?include_archived=true')).toEqual([]);
  });
});
