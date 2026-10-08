import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PluginPage } from './PluginPage';
import { PluginsPage } from './PluginsPage';
import { stubFetch } from '../test/fetchStub';
import { mkPlugin, pluginsBody } from '../test/inventoryFixtures';
import { resetPluginStore, type PluginSummary } from '../api/plugins';
import { PluginRedirects, pluginRedirectFor } from '../plugins/PluginRedirects';

const tabs = (...t: [string, string, 'default' | 'schema' | 'component'][]) =>
  ({ mode: 'page' as const, nav_label: 'Demo', nav_placement: 'settings' as const, nav_icon: null, tabs: t.map(([id, label, renderer]) => ({ id, label, renderer, ...(renderer === 'component' ? { component: 'material-mappings', requires: 'PROFILE_LINKS_READ' } : {}) })), redirects: [] });

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname}</div>; }

const show = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/plugins/:id" element={<><PluginPage /><Where /></>} />
      <Route path="/plugins/:id/:tab" element={<><PluginPage /><Where /></>} />
    </Routes>
  </MemoryRouter>);

const withPlugin = (p: PluginSummary, extra: Record<string, unknown> = {}) => stubFetch({
  'GET /api/v1/plugins': pluginsBody(p),
  [`GET /api/v1/plugins/${p.id}`]: { ...p, settings: {}, secrets: {}, secret_fields: [], settings_schema: { properties: {} }, state: {} },
  'GET /api/v1/inventory/sync-status': {}, 'GET /api/v1/inventory/pending-writes': { items: [] }, 'GET /api/v1/inventory/tracking': { items: [] },
  ...extra,
});

describe('PluginPage', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('opens the first tab when none is named, and renders each tab with its declared renderer', async () => {
    withPlugin(mkPlugin({ id: 'demo', name: 'Demo', ui: tabs(['conn', 'Connection', 'default'], ['lib', 'Library', 'schema']) }), {
      '/api/v1/plugins/demo/ui/lib': { blocks: [] },
    });
    show('/plugins/demo');

    expect(await screen.findByTestId('plugin-page')).toBeTruthy();                         // the default renderer = PluginSettingsPage
    expect(screen.getByTestId('where').textContent).toBe('/plugins/demo/conn');
    expect(screen.getByRole('link', { name: 'Library' }).getAttribute('href')).toBe('/plugins/demo/lib');
  });

  it('a schema tab is rendered by the schema renderer', async () => {
    withPlugin(mkPlugin({ id: 'demo', ui: tabs(['conn', 'Connection', 'default'], ['lib', 'Library', 'schema']) }), {
      'GET /api/v1/plugins/demo/ui/lib': { title: 'From the plugin', blocks: [] },
    });
    show('/plugins/demo/lib');
    expect(await screen.findByRole('heading', { name: 'From the plugin' })).toBeTruthy();
    expect(screen.queryByTestId('plugin-page')).toBeNull();
  });

  it('a component tab renders the registered component, and says so when the build has none', async () => {
    const ui = tabs(['x', 'X', 'component']);
    ui.tabs[0] = { ...ui.tabs[0], component: 'not-in-this-build', requires: undefined };
    withPlugin(mkPlugin({ id: 'nocomp', ui }));
    show('/plugins/nocomp/x');
    expect((await screen.findByRole('alert')).textContent).toMatch(/needs a component that is not part of this build/);
  });

  it('an unknown tab name falls back to the first tab', async () => {
    withPlugin(mkPlugin({ id: 'demo', ui: tabs(['conn', 'Connection', 'default'], ['lib', 'Library', 'schema']) }));
    show('/plugins/demo/nope');
    await screen.findByTestId('plugin-page');
    expect(screen.getByTestId('where').textContent).toBe('/plugins/demo/conn');
  });

  it('a plugin that declares no tabs gets the default page', async () => {
    withPlugin(mkPlugin({ id: 'demo', ui: tabs() }));
    show('/plugins/demo');
    expect(await screen.findByTestId('plugin-page')).toBeTruthy();
    expect(screen.getByTestId('where').textContent).toBe('/plugins/demo/default');
  });

  it.each([
    ['without preset-link support the mappings tab does not exist', ['TRACKS_WEIGHT'], false],
    ['with preset-link support the mappings tab is offered', ['PROFILE_LINKS_READ', 'PROFILE_LINKS_WRITE'], true],
  ])('%s', async (_name, capabilities, shown) => {
    withPlugin(mkPlugin({ id: 'spoolman', capabilities, ui: tabs(['connection', 'Connection', 'default'], ['mappings', 'Filament mappings', 'component']) }));
    show('/plugins/spoolman/connection');
    await screen.findByTestId('plugin-page');
    expect(!!screen.queryByRole('link', { name: 'Filament mappings' })).toBe(shown);
  });

  it('a plugin whose every tab is unavailable says so instead of crashing', async () => {
    withPlugin(mkPlugin({ id: 'spoolman', capabilities: [], ui: tabs(['mappings', 'Filament mappings', 'component']) }));
    show('/plugins/spoolman');
    expect(await screen.findByText(/has no pages available/)).toBeTruthy();
  });

  it('a disabled plugin has no pages, and an unknown one says so', async () => {
    const api = withPlugin(mkPlugin({ id: 'demo', enabled: false, active: false }), { 'PUT /api/v1/plugins/demo': { id: 'demo' } });
    show('/plugins/demo');
    expect(await screen.findByTestId('plugin-disabled')).toBeTruthy();
    expect(screen.queryByTestId('plugin-page')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Enable demo' }));            // not a dead end: it can be switched back on
    await waitFor(() => expect(api.to('PUT', '/api/v1/plugins/demo')[0].body).toEqual({ enabled: true }));

    resetPluginStore();
    show('/plugins/ghost');
    expect(await screen.findByText(/No plugin “ghost” is installed/)).toBeTruthy();
  });

  it('old URLs a plugin declares are redirected to that plugin\'s tab; others are left alone', () => {
    const p = mkPlugin({ id: 'acme', ui: { ...tabs(['conn', 'Connection', 'default']), redirects: [{ from: '/settings/acme', tab: 'conn' }] } });
    expect(pluginRedirectFor([mkPlugin({ id: 'other' }), p], '/settings/acme')).toBe('/plugins/acme/conn');
    expect(pluginRedirectFor([p], '/settings/elsewhere')).toBeNull();
    const hijack = mkPlugin({ id: 'evil', ui: { ...tabs(['c', 'C', 'default']), redirects: [{ from: '/queue', tab: 'c' }] } });
    expect(pluginRedirectFor([hijack], '/queue')).toBeNull();                              // core routes can't be claimed
  });
});

describe('PluginRedirects (mounted)', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('navigates an old URL to the declaring plugin\'s tab once plugins load', async () => {
    const p = mkPlugin({ id: 'demo', ui: { ...tabs(['conn', 'Connection', 'default']), redirects: [{ from: '/settings/demo', tab: 'conn' }] } });
    stubFetch({ 'GET /api/v1/plugins': pluginsBody(p) });
    render(<MemoryRouter initialEntries={['/settings/demo']}><PluginRedirects /><Where /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/plugins/demo/conn'));
  });
});

describe('PluginsPage (Settings → Plugins)', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('lists every plugin with its state; a page plugin links out, a section plugin expands in place', async () => {
    const small = mkPlugin({ id: 'small_one', name: 'Small', enabled: true, active: false, ui: { mode: 'section', nav_label: 'Small', nav_placement: 'settings', nav_icon: null, tabs: [] } });
    const big = mkPlugin({ id: 'big_one', name: 'Big', ui: tabs(['c', 'C', 'default']) });
    stubFetch({
      'GET /api/v1/plugins': { plugins: [big, small], selections: { 'inventory.filament': 'big_one' } },
      'GET /api/v1/plugins/small_one': { ...small, settings: {}, secrets: {}, secret_fields: [], settings_schema: { properties: {} }, state: {} },
    });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);

    expect((await screen.findByTestId('plugin-big_one')).textContent).toContain('Active');
    expect(screen.getByTestId('plugin-small_one').textContent).toContain('Enabled');
    expect(screen.getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('/plugins/big_one');

    await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByTestId('plugin-page')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Hide' }));
    expect(screen.queryByTestId('plugin-page')).toBeNull();
  });

  it('a disabled plugin is offered an Enable button instead of a page link (never a dead end)', async () => {
    const off = mkPlugin({ id: 'off_one', name: 'Off', enabled: false, active: false, ui: tabs(['c', 'C', 'default']) });
    const api = stubFetch({ 'GET /api/v1/plugins': { plugins: [off], selections: {} }, 'PUT /api/v1/plugins/off_one': { id: 'off_one' } });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);
    expect((await screen.findByTestId('plugin-off_one')).textContent).toContain('Disabled');
    expect(screen.queryByRole('link', { name: 'Open' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Enable' }));
    await waitFor(() => expect(api.to('PUT', '/api/v1/plugins/off_one')[0].body).toEqual({ enabled: true }));
  });

  it('shows a badge per capability a plugin provides, marking the ones it serves', async () => {
    const p = mkPlugin({ id: 'multi', name: 'Multi', description: '', provides: [
      { capability: 'inventory.filament', version: 1, features: [], selected: true, status: 'serving', waiting_on: [] },
      { capability: 'other.cap', version: 1, features: [], selected: false, status: 'not_selected', waiting_on: [] },
    ] });
    stubFetch({ 'GET /api/v1/plugins': { plugins: [p], selections: {} } });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);
    const badges = (await screen.findByTestId('plugin-caps-multi')).querySelectorAll('.pill');
    expect([...badges].map(b => [b.textContent, b.classList.contains('ok')])).toEqual([['inventory.filament', true], ['other.cap', false]]);
  });

  it('says so when nothing is installed', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [], selections: {} } });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);
    expect(await screen.findByText('No plugins are installed.')).toBeTruthy();
  });
});
