import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PluginSettingsPage } from './PluginSettingsPage';
import { Reply, stubFetch } from '../test/fetchStub';
import { mkPlugin, mkStatus, pluginsBody } from '../test/inventoryFixtures';
import { resetPluginStore, type PluginDetail } from '../api/plugins';

const detail = (over: Partial<PluginDetail> = {}): PluginDetail => ({
  ...mkPlugin({ id: 'demo_inv', name: 'Demo inventory', ...(over.active === false ? { active: false } : {}) }),
  description: 'A demo provider', docs_url: 'https://example.test/docs', version: '2.1',
  settings: { url: 'http://demo.test', sync_interval_minutes: 15, max_disconnect_minutes: null },
  secrets: { api_key: true },
  secret_fields: ['api_key'],
  settings_schema: {
    properties: {
      url: { type: 'string', title: 'Url', description: 'Where it lives' },
      api_key: { type: 'string', title: 'Api Key', description: 'Optional key' },
      sync_interval_minutes: { type: 'integer', title: 'Sync interval', minimum: 1, default: 15 },
      max_disconnect_minutes: { anyOf: [{ type: 'integer' }, { type: 'null' }], title: 'Alert after (min)' },
      verbose: { type: 'boolean', title: 'Verbose' },
    },
    required: ['url'],
  },
  state: {},
  ...over,
});

const routes = (d = detail(), over: Record<string, unknown> = {}) => ({
  'GET /api/v1/plugins': pluginsBody(d),
  'GET /api/v1/plugins/demo_inv': d,
  'GET /api/v1/inventory/sync-status': mkStatus({ provider: 'demo_inv' }),
  'GET /api/v1/inventory/pending-writes': { provider: 'demo_inv', items: [] },
  'GET /api/v1/inventory/tracking': { provider: 'demo_inv', items: [] },
  ...over,
});

describe('PluginSettingsPage', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('shows the header facts and builds the form from the settings schema (required marked, secret fields separate)', async () => {
    stubFetch(routes());
    render(<PluginSettingsPage pluginId="demo_inv" />);

    expect(await screen.findByRole('heading', { name: 'Demo inventory' })).toBeTruthy();
    expect(screen.getByText(/A demo provider · v2\.1/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Documentation' }).getAttribute('href')).toBe('https://example.test/docs');
    expect(screen.getByTestId('plugin-health').textContent).toBe('Active');
    expect((screen.getByLabelText('Url') as HTMLInputElement).value).toBe('http://demo.test');
    expect(screen.getByText('Url *')).toBeTruthy();                                        // required
    expect((screen.getByLabelText('Sync interval') as HTMLInputElement).type).toBe('number');
    expect((screen.getByLabelText('Alert after (min)') as HTMLInputElement).value).toBe('');   // nullable integer, unset
    expect(screen.getByRole('switch', { name: '' , checked: false })).toBeTruthy();       // the boolean (and the enable switch is checked)
  });

  it('never shows a stored secret; saving untouched sends no secrets, replacing sends the new value, clearing sends ""', async () => {
    const api = stubFetch(routes(detail(), { 'PUT /api/v1/plugins/demo_inv': (c: { body: unknown }) => ({ ...detail(), ...c.body as object }) }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    const key = await screen.findByLabelText('Api Key') as HTMLInputElement;
    expect(key.value).toBe('');
    expect(key.placeholder).toBe('Set ✓ — type to replace');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect((api.to('PUT', '/api/v1/plugins/demo_inv')[0].body as { secrets: object }).secrets).toEqual({});          // omitted = keep

    await userEvent.type(key, 'new-key');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/plugins/demo_inv')).toHaveLength(2));
    expect((api.to('PUT', '/api/v1/plugins/demo_inv')[1].body as { secrets: object }).secrets).toEqual({ api_key: 'new-key' });

    await userEvent.click(await screen.findByRole('button', { name: 'Clear Api Key' }));
    expect(screen.getByText('Will be cleared on save')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/plugins/demo_inv')).toHaveLength(3));
    expect((api.to('PUT', '/api/v1/plugins/demo_inv')[2].body as { secrets: object }).secrets).toEqual({ api_key: '' });
  });

  it('saves edited settings with numbers as numbers and blanks as null', async () => {
    const api = stubFetch(routes(detail(), { 'PUT /api/v1/plugins/demo_inv': detail() }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    const interval = await screen.findByLabelText('Sync interval');

    await userEvent.clear(interval);
    await userEvent.type(interval, '30');
    await userEvent.type(screen.getByLabelText('Alert after (min)'), '45');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await screen.findByRole('status');
    expect((api.to('PUT', '/api/v1/plugins/demo_inv')[0].body as { settings: Record<string, unknown> }).settings)
      .toMatchObject({ url: 'http://demo.test', sync_interval_minutes: 30, max_disconnect_minutes: 45 });
  });

  it('Test connection tries the unsaved form values and shows the result without saving', async () => {
    const api = stubFetch(routes(detail(), { 'POST /api/v1/plugins/demo_inv/test': { ok: true, version: '1.9' } }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    const url = await screen.findByLabelText('Url');

    await userEvent.clear(url);
    await userEvent.type(url, 'http://other.test');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    expect((await screen.findByTestId('test-result')).textContent).toBe('Connected (version 1.9)');
    expect((api.to('POST', '/api/v1/plugins/demo_inv/test')[0].body as { settings: { url: string } }).settings.url).toBe('http://other.test');
    expect(api.to('PUT', '/api/v1/plugins/demo_inv')).toEqual([]);                          // nothing saved
  });

  it('reports a failed test and a rejected save', async () => {
    stubFetch(routes(detail(), {
      'POST /api/v1/plugins/demo_inv/test': { ok: false, message: 'connection refused' },
      'PUT /api/v1/plugins/demo_inv': new Reply(422, { detail: 'sync interval must be >= 1' }),
    }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    await screen.findByLabelText('Url');

    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect((await screen.findByTestId('test-result')).textContent).toBe('connection refused');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toBe('sync interval must be >= 1');
  });

  it('enables and disables the plugin from the header switch', async () => {
    const api = stubFetch(routes(detail(), { 'PUT /api/v1/plugins/demo_inv': (c: { body: { enabled: boolean } }) => detail({ enabled: c.body.enabled, active: false }) }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    const enable = await screen.findByRole('switch', { name: '', checked: true });

    await userEvent.click(enable);

    await waitFor(() => expect(api.to('PUT', '/api/v1/plugins/demo_inv')[0].body).toEqual({ enabled: false }));
    expect((await screen.findByTestId('plugin-health')).textContent).toBe('Disabled');
  });

  it('offers to make an enabled plugin the active provider when another one is selected', async () => {
    const d = detail({ active: false });
    const api = stubFetch(routes(d, {
      'GET /api/v1/plugins': { plugins: [d], selections: { 'inventory.filament': 'someone_else' } },
      'PUT /api/v1/capabilities/inventory.filament/provider': { capability: 'inventory.filament', plugin_id: 'demo_inv', explicit: true },
    }));
    render(<PluginSettingsPage pluginId="demo_inv" />);

    expect(await screen.findByText('Enabled, not selected')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Use Demo inventory' }));

    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/inventory.filament/provider')[0].body).toEqual({ plugin_id: 'demo_inv' }));
  });

  it('shows a plugin problem, and a load failure, instead of the form', async () => {
    stubFetch(routes(detail({ error: 'could not build provider: bad url' })));
    const { unmount } = render(<PluginSettingsPage pluginId="demo_inv" />);
    expect((await screen.findByTestId('plugin-health')).textContent).toBe('Problem');
    expect(screen.getByText('could not build provider: bad url')).toBeTruthy();
    unmount();

    stubFetch({ 'GET /api/v1/plugins': { plugins: [], selections: {} }, 'GET /api/v1/plugins/demo_inv': new Reply(404, { detail: "Unknown plugin 'demo_inv'" }) });
    resetPluginStore();
    render(<PluginSettingsPage pluginId="demo_inv" />);
    expect((await screen.findByRole('alert')).textContent).toBe("Unknown plugin 'demo_inv'");
  });

  it('only an active inventory provider gets the sync / queued-writes panel', async () => {
    stubFetch(routes(detail({ active: false })));
    const { unmount } = render(<PluginSettingsPage pluginId="demo_inv" />);
    await screen.findByLabelText('Url');
    expect(screen.queryByTestId('inventory-panel')).toBeNull();
    unmount();

    stubFetch(routes());
    render(<PluginSettingsPage pluginId="demo_inv" />);
    expect(await screen.findByTestId('inventory-panel')).toBeTruthy();
    expect(within(screen.getByTestId('inventory-panel')).getByText('Queued weight updates')).toBeTruthy();
  });

  it('a plugin serving several capabilities gets one "Use for" button per capability it is not selected for', async () => {
    const base = mkPlugin({ id: 'demo_inv', name: 'Demo inventory', active: false });
    const d = detail({ active: false, provides: [
      { ...base.provides[0], selected: false },
      { capability: 'demo_inv.notes', version: 1, features: [], selected: false, status: 'not_selected', waiting_on: [] }] });
    const api = stubFetch(routes(d, { 'PUT /api/v1/capabilities/demo_inv.notes/provider': { capability: 'demo_inv.notes', plugin_id: 'demo_inv', explicit: true } }));
    render(<PluginSettingsPage pluginId="demo_inv" />);

    await userEvent.click(await screen.findByRole('button', { name: 'Use Demo inventory for demo_inv.notes' }));

    expect(screen.getByRole('button', { name: 'Use Demo inventory for inventory.filament' })).toBeTruthy();
    await waitFor(() => expect(api.to('PUT', '/api/v1/capabilities/demo_inv.notes/provider')[0].body).toEqual({ plugin_id: 'demo_inv' }));
  });

  it('says what a waiting capability is waiting on', async () => {
    const d = detail({ provides: [{ capability: 'demo_inv.report', version: 1, features: [], selected: true, status: 'waiting', waiting_on: ['demo_inv.notes'] }] });
    stubFetch(routes(d));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    expect(await screen.findByText('demo_inv.report: waiting on demo_inv.notes')).toBeTruthy();
    expect(screen.queryByTestId('inventory-panel')).toBeNull();
  });

  it('after "Use", the page reflects the new selection without navigating away', async () => {
    let selected = false;
    const make = () => detail({ active: selected, provides: mkPlugin({ id: 'demo_inv', active: selected }).provides });
    stubFetch(routes(detail({ active: false }), {
      'GET /api/v1/plugins/demo_inv': () => make(),
      'GET /api/v1/plugins': () => ({ plugins: [make()], selections: { 'inventory.filament': selected ? 'demo_inv' : 'someone_else' } }),
      'PUT /api/v1/capabilities/inventory.filament/provider': () => { selected = true; return { capability: 'inventory.filament', plugin_id: 'demo_inv', explicit: true }; },
    }));
    render(<PluginSettingsPage pluginId="demo_inv" />);
    expect(await screen.findByText('Enabled, not selected')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Use Demo inventory' }));

    await waitFor(() => expect(screen.getByTestId('plugin-health').textContent).toBe('Active'));
    expect(screen.queryByRole('button', { name: 'Use Demo inventory' })).toBeNull();
    expect(await screen.findByTestId('inventory-panel')).toBeTruthy();
  });
});
