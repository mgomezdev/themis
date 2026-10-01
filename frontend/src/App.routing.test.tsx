import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { stubFetch } from './test/fetchStub';

// The routed screens are replaced by markers: this file is about App.tsx itself (route table, redirects,
// top-bar title/actions, role switch, nav badges, search shortcut), not the screens' own data fetching.
vi.mock('./screens/QueueScreen',          async () => ({ QueueScreen:          (await import('./test/markers')).marker('Queue screen') }));
vi.mock('./screens/FleetScreen',          async () => ({ FleetScreen:          (await import('./test/markers')).marker('Fleet screen') }));
vi.mock('./screens/OrdersScreen',         async () => ({ OrdersScreen:         (await import('./test/markers')).marker('Orders screen') }));
vi.mock('./screens/NewJobScreen',         async () => ({ NewJobScreen:         (await import('./test/markers')).marker('New job screen') }));
vi.mock('./screens/NewOrderScreen',       async () => ({ NewOrderScreen:       (await import('./test/markers')).marker('Order form') }));
vi.mock('./screens/JobDetailScreen',      async () => ({ JobDetailScreen:      (await import('./test/markers')).marker('Job detail') }));
vi.mock('./screens/EditJobScreen',        async () => ({ EditJobScreen:        (await import('./test/markers')).marker('Edit job') }));
vi.mock('./screens/FilesScreen',          async () => ({ FilesScreen:          (await import('./test/markers')).marker('Files screen') }));
vi.mock('./screens/SettingsScreen',       async () => ({ SettingsScreen:       (await import('./test/markers')).marker('Settings screen', { splat: true }) }));
vi.mock('./screens/ProjectsScreen',       async () => ({ ProjectsScreen:       (await import('./test/markers')).marker('Projects screen') }));
vi.mock('./screens/ProjectBuilderScreen', async () => ({ ProjectBuilderScreen: (await import('./test/markers')).marker('Project builder') }));
vi.mock('./screens/ProjectDetailScreen',  async () => ({ ProjectDetailScreen:  (await import('./test/markers')).marker('Project detail') }));
vi.mock('./screens/HistoryScreen',        async () => ({ HistoryScreen:        (await import('./test/markers')).marker('History screen') }));
vi.mock('./screens/SharedProjectScreen',  async () => ({ SharedProjectScreen:  (await import('./test/markers')).marker('Shared project') }));
vi.mock('./screens/CustomerPortal',       async () => ({ CustomerPortal:       (await import('./test/markers')).marker('Customer portal') }));

class FakeWS { onmessage: unknown = null; close() {} }

const job = (id: number, status: string) => ({ id, status, queue_position: id });
const fleetPrinter = (id: number, name: string) => ({
  id, name, printer_type: 'bambu', enabled: true, queue_on: true, connected: true, awaiting_plate_clear: false,
  no_snapshots_while_idle: false, loaded_filaments: [], state: 'IDLE', progress: 0, remaining_time: 0,
  layer_num: null, total_layers: null, temperatures: {}, capabilities: {}, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0,
});
const STAFF = { local: false, role: 'staff', customer: null };

function boot(path: string, over: Record<string, unknown> = {}) {
  localStorage.setItem('themis.apiKey', 'sk-test');      // straight past the AuthGate
  const api = stubFetch({
    'GET /api/v1/auth/me': STAFF,
    'GET /api/v1/queue': [],
    'GET /api/v1/settings/queue': { operator_name: null },
    'GET /api/v1/fleet': [],
    'GET /api/v1/settings/spoolman': { enabled: false, url: '' },
    'GET /api/v1/spoolman/sync-status': { enabled: false },
    'GET /api/v1/laminus/catalog/status': { laminus_configured: false, laminus: null },
    'GET /api/v1/orders': [],
    'GET /api/v1/files': [],
    ...over,
  });
  window.history.pushState({}, '', path);
  render(<App />);
  return api;
}
const screenText = () => screen.findByTestId('screen').then(el => el.textContent);
/** Waits for a routed screen and flushes the shell's mount effects (e.g. the Ctrl+K listener attaches in one). */
const shellReady = async () => { await screenText(); await act(async () => {}); };
const pathname = () => window.location.pathname;
const topbar = () => document.querySelector('.topbar') as HTMLElement;
const title = () => topbar().querySelector('h1')!.textContent;
const crumbs = () => Array.from(topbar().querySelectorAll('.crumb')).map(c => c.textContent);

beforeEach(() => { vi.stubGlobal('WebSocket', FakeWS); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.history.pushState({}, '', '/'); });   // unmount while fetch is still stubbed

describe('App - route table', () => {
  it.each([
    ['/queue', 'Queue screen'],
    ['/queue/new', 'New job screen'],
    ['/fleet', 'Fleet screen'],
    ['/orders', 'Orders screen'],
    ['/orders/new', 'Order form'],
    ['/orders/5/edit', 'Order form [id=5]'],
    ['/jobs/12', 'Job detail [id=12]'],
    ['/jobs/12/edit', 'Edit job [id=12]'],
    ['/files', 'Files screen'],
    ['/projects', 'Projects screen'],
    ['/projects/new', 'Project builder'],
    ['/projects/8', 'Project detail [id=8]'],
    ['/projects/8/edit', 'Project builder [id=8]'],
    ['/history', 'History screen'],
    ['/settings', 'Settings screen [rest=]'],
    ['/settings/tags', 'Settings screen [rest=tags]'],
    ['/settings/api-keys', 'Settings screen [rest=api-keys]'],
  ])('%s shows %s', async (path, expected) => {
    boot(path);

    expect(await screenText()).toBe(expected);
    expect(pathname()).toBe(path);                       // no stray redirect
  });

  it.each(['/', '/nowhere', '/orders/5/oops/deeper', '/queue/unknown'])('%s is redirected to /queue', async path => {
    boot(path);

    expect(await screenText()).toBe('Queue screen');
    expect(pathname()).toBe('/queue');
  });
});

describe('App - top bar', () => {
  it.each([
    ['/queue', 'Job queue', ['Workshop']],
    ['/queue/new', 'New job', ['Workshop', 'Job queue']],
    ['/fleet', 'Fleet', ['Workshop']],
    ['/orders', 'Orders', ['Workshop']],
    ['/orders/new', 'New order', ['Workshop', 'Orders']],
    ['/orders/5/edit', 'Edit order', ['Workshop', 'Orders']],
    ['/jobs/12', 'Job details', ['Workshop', 'Job queue']],
    ['/jobs/12/edit', 'Edit job settings', ['Workshop', 'Job queue']],
    ['/files', 'Model library', ['Workshop']],
    ['/projects', 'Projects', ['Workshop']],
    ['/projects/new', 'New project', ['Workshop', 'Projects']],
    ['/projects/8', 'Project', ['Workshop', 'Projects']],
    ['/projects/8/edit', 'Edit project', ['Workshop', 'Projects']],
    ['/history', 'History', ['Workshop']],
    ['/settings', 'Settings', []],
    ['/settings/tags', 'Settings', []],
    ['/settings/webhook', 'Settings', []],
  ])('%s is titled "%s"', async (path, expectedTitle, expectedCrumbs) => {
    boot(path);
    await screenText();

    expect(title()).toBe(expectedTitle);
    expect(crumbs()).toEqual(expectedCrumbs);
  });

  it.each([
    ['/queue', 'New job', '/queue/new'],
    ['/orders', 'New order', '/orders/new'],
    ['/projects', 'New project', '/projects/new'],
  ])('%s has a "%s" button that opens %s', async (path, label, target) => {
    boot(path);
    await screenText();

    await userEvent.click(within(topbar()).getByRole('button', { name: new RegExp(label) }));

    expect(pathname()).toBe(target);
  });

  it('offers no actions on screens that do not have any', async () => {
    boot('/history');
    await screenText();

    expect(within(topbar()).queryAllByRole('button')).toEqual([]);
  });
});

describe('App - who sees what', () => {
  it('gives customers the portal and none of the staff shell or staff requests', async () => {
    const api = boot('/queue', { 'GET /api/v1/auth/me': { local: false, role: 'customer', customer: { id: 1, name: 'Vela', email: 'v@x.test' } } });

    expect(await screenText()).toBe('Customer portal');
    expect(document.querySelector('.sidebar')).toBeNull();
    expect(api.to('GET', '/api/v1/queue')).toEqual([]);
    expect(api.to('GET', '/api/v1/fleet')).toEqual([]);
  });

  it.each(['admin', 'staff'])('gives %s sessions the full app', async role => {
    boot('/queue', { 'GET /api/v1/auth/me': { local: role === 'admin', role, customer: null } });

    expect(await screenText()).toBe('Queue screen');
    expect(document.querySelector('.sidebar')).not.toBeNull();
  });

  it('falls back to the full app when the session cannot be read (the API rejects with 401 elsewhere)', async () => {
    boot('/queue', { 'GET /api/v1/auth/me': { unexpected: true } });

    expect(await screenText()).toBe('Queue screen');
  });

  it('renders nothing (and makes no staff requests) until the role is known', async () => {
    let release!: () => void;
    const gate = new Promise<void>(r => { release = r; });
    const api = boot('/queue', { 'GET /api/v1/auth/me': () => gate.then(() => STAFF) });

    await act(async () => {});
    expect(document.querySelector('.sidebar')).toBeNull();
    expect(api.to('GET', '/api/v1/queue')).toEqual([]);

    release();
    expect(await screenText()).toBe('Queue screen');
  });

  it('serves /share/:token without any session at all', async () => {
    localStorage.clear();
    stubFetch({});
    window.history.pushState({}, '', '/share/abc');
    render(<App />);

    expect(await screenText()).toBe('Shared project [token=abc]');
    expect(document.querySelector('.sidebar')).toBeNull();
  });
});

describe('App - navigation chrome', () => {
  it('shows the operator name and printer count in the sidebar', async () => {
    boot('/fleet', {
      'GET /api/v1/settings/queue': { operator_name: 'Ada Lovelace' },
      'GET /api/v1/fleet': [fleetPrinter(1, 'A'), fleetPrinter(2, 'B')],
    });
    await screenText();

    expect(await screen.findByText('Ada Lovelace')).toBeTruthy();
    expect(screen.getByText('AL')).toBeTruthy();
    expect(await screen.findByText('2 printers')).toBeTruthy();
  });

  it('counts jobs into active / pending / blocked badges (sidebar and bottom bar)', async () => {
    boot('/fleet', {
      'GET /api/v1/queue': [
        job(1, 'printing'), job(2, 'paused'), job(3, 'slicing'), job(4, 'uploading'),
        job(5, 'queued'), job(6, 'queued'),
        job(7, 'blocked'),
        job(8, 'complete'), job(9, 'cancelled'), job(10, 'failed'),                 // not counted anywhere
      ],
    });
    await screenText();

    expect((await screen.findByTestId('badge-active')).textContent).toBe('4');
    expect(screen.getByTestId('badge-pending').textContent).toBe('2');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
    const counts = document.querySelectorAll('.bn-count');
    expect(Array.from(counts).map(c => c.textContent)).toEqual(['7']);            // 4 + 2 + 1, on the Queue item only
    expect(counts[0].closest('button')!.textContent).toContain('Queue');
  });

  it('shows no badges for an empty queue', async () => {
    boot('/fleet');
    await screenText();

    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(document.querySelector('.bn-count')).toBeNull();
  });

  it('bottom bar highlights the section of the current path and navigates', async () => {
    boot('/projects/8/edit');
    await screenText();
    const bar = document.querySelector('nav.bottom-nav') as HTMLElement;
    const active = () => Array.from(bar.querySelectorAll('.active')).map(b => b.textContent);

    expect(active()).toEqual(['Projects']);
    await userEvent.click(within(bar).getByRole('button', { name: 'Fleet' }));

    expect(pathname()).toBe('/fleet');
    expect(active()).toEqual(['Fleet']);
  });

  it('bottom bar "More" sheet reaches the screens with no slot, highlights the bar, and closes on navigation', async () => {
    boot('/fleet');
    await screenText();
    const bar = document.querySelector('nav.bottom-nav') as HTMLElement;

    expect(screen.queryByRole('menu')).toBeNull();
    await userEvent.click(within(bar).getByRole('button', { name: 'More' }));
    const menu = screen.getByRole('menu', { name: 'More destinations' });
    expect(within(menu).getAllByRole('menuitem').map(i => i.textContent)).toEqual(['Customers', 'Files', 'Printer files', 'Camera wall', 'Alarms', 'History', 'Analytics']);

    await userEvent.click(within(menu).getByRole('menuitem', { name: 'History' }));

    expect(pathname()).toBe('/history');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(Array.from(bar.querySelectorAll('.active')).map(b => b.textContent)).toEqual(['More']);
  });

  it('sidebar lists the settings pages only while on a settings route', async () => {
    boot('/fleet');
    await screenText();
    expect(screen.queryByRole('link', { name: 'API Keys' })).toBeNull();

    await userEvent.click(screen.getByRole('link', { name: 'Settings' }));

    expect(await screen.findByRole('link', { name: 'API Keys' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Tags' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Filament Mappings' })).toBeNull();   // only once Spoolman is on
  });

  it('opening settings re-expands a collapsed sidebar so its pages are reachable', async () => {
    boot('/fleet');
    await screenText();
    const app = document.querySelector('.app') as HTMLElement;
    expect(app.getAttribute('data-nav')).toBe('expanded');

    await userEvent.click(screen.getByTitle('Collapse sidebar'));
    expect(app.getAttribute('data-nav')).toBe('collapsed');
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }));

    await waitFor(() => expect(app.getAttribute('data-nav')).toBe('expanded'));
  });
});

describe('App - search shortcut', () => {
  const search = () => screen.queryByPlaceholderText('Search jobs, orders, files, printers…');

  it.each([{ ctrlKey: true }, { metaKey: true }])('%o + K opens the search box, Escape closes it', async mods => {
    boot('/fleet');
    await shellReady();
    expect(search()).toBeNull();

    fireEvent.keyDown(window, { key: 'k', ...mods });
    expect(await screen.findByPlaceholderText('Search jobs, orders, files, printers…')).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(search()).toBeNull());
  });

  it('swallows the browser default for the shortcut (Ctrl+K would otherwise focus the address bar)', async () => {
    boot('/fleet');
    await shellReady();

    const notPrevented = fireEvent.keyDown(window, { key: 'k', ctrlKey: true });

    expect(notPrevented).toBe(false);
  });

  it('a bare K does nothing', async () => {
    boot('/fleet');
    await shellReady();

    fireEvent.keyDown(window, { key: 'k' });

    expect(search()).toBeNull();
  });
});

describe('App - service health', () => {
  const footer = () => document.querySelector('.main > div:last-child') as HTMLElement;
  const dot = () => (within(footer()).getByText('Laminus').firstElementChild as HTMLElement).style.background;

  it.each([
    [{ laminus_configured: false, laminus: null }, 'var(--idle)'],
    [{ laminus_configured: true, laminus: { status: 'ok' } }, 'var(--ok)'],
    [{ laminus_configured: true, laminus: null }, 'var(--err)'],
  ])('shows Laminus as %o', async (status, colour) => {
    boot('/fleet', { 'GET /api/v1/laminus/catalog/status': status });
    await screenText();

    await waitFor(() => expect(dot()).toBe(colour));
  });

  it('re-checks Laminus on a 30 second interval', async () => {
    // Capture the registered interval instead of faking timers (faking setInterval also stops waitFor polling).
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    try {
      const api = boot('/fleet');
      await screenText();
      const seen = () => api.to('GET', '/api/v1/laminus/catalog/status').length;
      await waitFor(() => expect(seen()).toBeGreaterThan(0));
      // (the Spoolman sync-status poll also ticks every 30 s, so fire every such callback and count Laminus requests)
      const polls = setIntervalSpy.mock.calls.filter(([, ms]) => ms === 30_000).map(([fn]) => fn as () => void);
      expect(polls.length).toBeGreaterThan(0);
      const before = seen();

      await act(async () => { polls.forEach(fn => fn()); });

      expect(seen()).toBe(before + 1);
    } finally {
      setIntervalSpy.mockRestore();
    }
  });

  it('shows Laminus as down when the status endpoint fails', async () => {
    const { Reply } = await import('./test/fetchStub');
    boot('/fleet', { 'GET /api/v1/laminus/catalog/status': new Reply(500, 'boom') });
    await screenText();

    await waitFor(() => expect(dot()).toBe('var(--err)'));
  });
});
