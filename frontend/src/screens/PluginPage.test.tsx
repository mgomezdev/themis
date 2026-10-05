import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PluginPage } from './PluginPage';
import { PluginsPage } from './PluginsPage';
import { stubFetch } from '../test/fetchStub';
import { mkPlugin, pluginsBody } from '../test/inventoryFixtures';
import { resetPluginStore, type PluginSummary } from '../api/plugins';
import { LEGACY_REDIRECTS } from '../plugins/registry';

const tabs = (...t: [string, string, 'default' | 'schema' | 'component'][]) =>
  ({ mode: 'page' as const, nav_label: 'Demo', nav_placement: 'settings' as const, nav_icon: null, tabs: t.map(([id, label, renderer]) => ({ id, label, renderer })) });

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
    withPlugin(mkPlugin({ id: 'nocomp', ui: tabs(['x', 'X', 'component']) }));
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
    withPlugin(mkPlugin({ id: 'demo', enabled: false, active: false }));
    show('/plugins/demo');
    expect(await screen.findByTestId('plugin-disabled')).toBeTruthy();
    expect(screen.queryByTestId('plugin-page')).toBeNull();

    resetPluginStore();
    show('/plugins/ghost');
    expect(await screen.findByText(/No plugin “ghost” is installed/)).toBeTruthy();
  });

  it('the old settings URLs are redirected to their plugin pages', () => {
    expect(LEGACY_REDIRECTS).toEqual({
      '/settings/spoolman': '/plugins/spoolman/connection',
      '/settings/spoolman-mappings': '/plugins/spoolman/mappings',
    });
  });
});

describe('PluginsPage (Settings → Plugins)', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('lists every plugin with its state; a page plugin links out, a section plugin expands in place', async () => {
    const small = mkPlugin({ id: 'small_one', name: 'Small', enabled: false, active: false, ui: { mode: 'section', nav_label: 'Small', nav_placement: 'settings', nav_icon: null, tabs: [] } });
    const big = mkPlugin({ id: 'big_one', name: 'Big', ui: tabs(['c', 'C', 'default']) });
    stubFetch({
      'GET /api/v1/plugins': { plugins: [big, small], slots: { filament_inventory: 'big_one' } },
      'GET /api/v1/plugins/small_one': { ...small, settings: {}, secrets: {}, secret_fields: [], settings_schema: { properties: {} }, state: {} },
    });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);

    expect((await screen.findByTestId('plugin-big_one')).textContent).toContain('Active');
    expect(screen.getByTestId('plugin-small_one').textContent).toContain('Disabled');
    expect(screen.getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('/plugins/big_one');

    await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByTestId('plugin-page')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Hide' }));
    expect(screen.queryByTestId('plugin-page')).toBeNull();
  });

  it('says so when nothing is installed', async () => {
    stubFetch({ 'GET /api/v1/plugins': { plugins: [], slots: {} } });
    render(<MemoryRouter><PluginsPage /></MemoryRouter>);
    expect(await screen.findByText('No plugins are installed.')).toBeTruthy();
  });
});
