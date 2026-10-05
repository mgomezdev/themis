import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilamentInventoryPage } from './FilamentInventoryPage';
import { Reply, stubFetch } from '../test/fetchStub';
import { mkMaterial, mkPlugin } from '../test/inventoryFixtures';
import { resetPluginStore } from '../api/plugins';

const SETTINGS = { provider: 'p', deduct_on_complete: true, low_stock: { default_g: 100, overrides: {} } };

const render_ = () => render(<MemoryRouter><FilamentInventoryPage /></MemoryRouter>);
const base = (plugins: ReturnType<typeof mkPlugin>[], slot: string | null, over: Record<string, unknown> = {}) => ({
  'GET /api/v1/plugins': { plugins, slots: { filament_inventory: slot } },
  'GET /api/v1/inventory/settings': SETTINGS,
  'GET /api/v1/inventory/materials': { provider: 'p', stale: false, as_of: 'x', items: [mkMaterial({ ref: '1', name: 'PLA' })] },
  ...over,
});

describe('FilamentInventoryPage', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('with no provider: only the picker, plus a note that spool features are hidden', async () => {
    stubFetch(base([mkPlugin({ id: 'spoolman', active: false, enabled: false }), mkPlugin({ id: 'local_inv', active: false, enabled: false })], null));
    render_();

    const picker = await screen.findByLabelText('Inventory provider') as HTMLSelectElement;
    expect(picker.value).toBe('');
    expect(Array.from(picker.options).map(o => o.textContent)).toEqual(['None', 'spoolman', 'local_inv']);
    expect(screen.getByText(/No provider is selected/)).toBeTruthy();
    expect(screen.queryByText('Deduct filament when a job completes')).toBeNull();
    expect(screen.queryByTestId('low-stock')).toBeNull();
  });

  it('selecting a provider enables it first, then makes it the active one', async () => {
    const api = stubFetch(base([mkPlugin({ id: 'spoolman', active: false, enabled: false })], null, {
      'PUT /api/v1/plugins/spoolman': { id: 'spoolman' },
      'PUT /api/v1/extension-slots/filament_inventory': { kind: 'filament_inventory', plugin_id: 'spoolman' },
    }));
    render_();

    await userEvent.selectOptions(await screen.findByLabelText('Inventory provider'), 'spoolman');

    await waitFor(() => expect(api.to('PUT', '/api/v1/extension-slots/filament_inventory')).toHaveLength(1));
    expect(api.to('PUT', '/api/v1/plugins/spoolman')[0].body).toEqual({ enabled: true });
    expect(api.to('PUT', '/api/v1/extension-slots/filament_inventory')[0].body).toEqual({ plugin_id: 'spoolman' });
    // order: enable, then select
    expect(api.calls.findIndex(c => c.method === 'PUT' && c.url === '/api/v1/plugins/spoolman'))
      .toBeLessThan(api.calls.findIndex(c => c.method === 'PUT' && c.url === '/api/v1/extension-slots/filament_inventory'));
  });

  it('choosing None clears the slot', async () => {
    const api = stubFetch(base([mkPlugin({ id: 'spoolman' })], 'spoolman', { 'PUT /api/v1/extension-slots/filament_inventory': { kind: 'filament_inventory', plugin_id: null } }));
    render_();
    await userEvent.selectOptions(await screen.findByLabelText('Inventory provider'), '');
    await waitFor(() => expect(api.to('PUT', '/api/v1/extension-slots/filament_inventory')[0].body).toEqual({ plugin_id: null }));
  });

  it('with a capable provider: link to its page, the deduct switch and the thresholds', async () => {
    stubFetch(base([mkPlugin({ id: 'spoolman', name: 'Spoolman' })], 'spoolman'));
    render_();

    expect((await screen.findByRole('link', { name: /Open Spoolman settings/ })).getAttribute('href')).toBe('/plugins/spoolman');
    expect(await screen.findByRole('switch', { checked: true })).toBeTruthy();
    expect(await screen.findByTestId('low-stock')).toBeTruthy();
  });

  it('toggling deduct saves the real deduct_on_complete setting', async () => {
    const api = stubFetch(base([mkPlugin({ id: 'spoolman' })], 'spoolman', {
      'PUT /api/v1/inventory/settings': (c: { body: { deduct_on_complete: boolean } }) => ({ ...SETTINGS, deduct_on_complete: c.body.deduct_on_complete }),
    }));
    render_();
    await userEvent.click(await screen.findByRole('switch', { checked: true }));

    await waitFor(() => expect(api.to('PUT', '/api/v1/inventory/settings')[0].body).toEqual({ deduct_on_complete: false }));
    expect(await screen.findByRole('switch', { checked: false })).toBeTruthy();
  });

  it('a failed deduct save puts the switch back and shows why', async () => {
    stubFetch(base([mkPlugin({ id: 'spoolman' })], 'spoolman', { 'PUT /api/v1/inventory/settings': new Reply(422, { detail: 'nope' }) }));
    render_();
    await userEvent.click(await screen.findByRole('switch', { checked: true }));

    expect((await screen.findByRole('alert')).textContent).toBe('nope');
    expect(screen.getByRole('switch', { checked: true })).toBeTruthy();
  });

  it('hides deduction and thresholds when the provider cannot track or write weight', async () => {
    stubFetch(base([mkPlugin({ id: 'plain', capabilities: [] })], 'plain'));
    render_();
    await screen.findByLabelText('Inventory provider');
    expect(screen.queryByText('Deduct filament when a job completes')).toBeNull();
    expect(screen.queryByTestId('low-stock')).toBeNull();
  });

  it('a provider that tracks weight but cannot write it has thresholds but no deduct switch', async () => {
    stubFetch(base([mkPlugin({ id: 'ro', capabilities: ['TRACKS_WEIGHT'] })], 'ro'));
    render_();
    expect(await screen.findByTestId('low-stock')).toBeTruthy();
    expect(screen.queryByText('Deduct filament when a job completes')).toBeNull();
  });
});
