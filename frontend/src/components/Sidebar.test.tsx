import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { resetPluginStore } from '../api/plugins';
import { mkPlugin, mkStatus } from '../test/inventoryFixtures';

beforeEach(() => {
  resetPluginStore();
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    new Response(JSON.stringify(url === '/api/v1/plugins' ? { plugins: [], slots: {} } : {}), { status: 200 })
  ));
});

// Renders on /fleet so the Job Queue nav item is NOT active, giving unambiguous badge colors.
function renderOnFleet(
  active: number, pending: number, blocked: number,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/fleet']}>
      <Sidebar queueCounts={{ active, pending, blocked }}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}

// Renders on /queue so the Job Queue nav item IS active (accent-color override in CSS).
function renderOnQueue(
  active: number, pending: number, blocked: number,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/queue']}>
      <Sidebar queueCounts={{ active, pending, blocked }}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}

// ─── Nav structure ────────────────────────────────────────────────────────────

describe('Sidebar nav items', () => {
  it('renders all nav labels', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.getByText('Job queue')).toBeTruthy();
    expect(screen.getByText('Fleet')).toBeTruthy();
    expect(screen.getByText('Projects')).toBeTruthy();
    expect(screen.getByText('Files')).toBeTruthy();
    expect(screen.getByText('Settings')).toBeTruthy();
  });

  it('links Analytics to /analytics', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.getByText('Analytics').closest('a')?.getAttribute('href')).toBe('/analytics');
  });
});

// ─── Queue badges — empty queue ───────────────────────────────────────────────

describe('Queue badges — empty queue', () => {
  it('shows no badges when all counts are zero', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('never renders a badge with text "0"', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.queryByText('0')).toBeNull();
  });
});

// ─── Queue badges — individual types ─────────────────────────────────────────

describe('Queue badges — individual types', () => {
  it('shows active badge (green) when jobs are in progress', () => {
    renderOnFleet(3, 0, 0);
    const badge = screen.getByTestId('badge-active');
    expect(badge.textContent).toBe('3');
    expect(badge.style.color).toBe('var(--ok)');
    expect(badge.style.background).toBe('var(--ok-bg)');
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows pending badge (neutral) when jobs are queued', () => {
    renderOnFleet(0, 5, 0);
    const badge = screen.getByTestId('badge-pending');
    expect(badge.textContent).toBe('5');
    // Pending uses the default .count class styling (no inline color override)
    expect(badge.style.color).toBe('');
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows blocked badge (red) when jobs are blocked', () => {
    renderOnFleet(0, 0, 2);
    const badge = screen.getByTestId('badge-blocked');
    expect(badge.textContent).toBe('2');
    expect(badge.style.color).toBe('var(--err)');
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
  });
});

// ─── Queue badges — combinations ──────────────────────────────────────────────

describe('Queue badges — combinations', () => {
  it('shows all three badges simultaneously with correct counts', () => {
    renderOnFleet(2, 5, 1);
    expect(screen.getByTestId('badge-active').textContent).toBe('2');
    expect(screen.getByTestId('badge-pending').textContent).toBe('5');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
  });

  it('shows only active and pending when blocked is zero', () => {
    renderOnFleet(1, 4, 0);
    expect(screen.getByTestId('badge-active').textContent).toBe('1');
    expect(screen.getByTestId('badge-pending').textContent).toBe('4');
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows only active and blocked when pending is zero', () => {
    renderOnFleet(1, 0, 3);
    expect(screen.getByTestId('badge-active').textContent).toBe('1');
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.getByTestId('badge-blocked').textContent).toBe('3');
  });

  it('shows only pending and blocked when active is zero', () => {
    renderOnFleet(0, 7, 2);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.getByTestId('badge-pending').textContent).toBe('7');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('2');
  });
});

// ─── Queue badges — active nav item ───────────────────────────────────────────

describe('Queue badges — active nav item (/queue page)', () => {
  it('still renders all three badges when on the queue page', () => {
    renderOnQueue(2, 3, 1);
    expect(screen.getByTestId('badge-active').textContent).toBe('2');
    expect(screen.getByTestId('badge-pending').textContent).toBe('3');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
  });

  it('shows no badges when queue is empty, even on the queue page', () => {
    renderOnQueue(0, 0, 0);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });
});

// ─── Status semantics ─────────────────────────────────────────────────────────

describe('Queue badge status semantics', () => {
  it('counts active correctly for all in-progress statuses', () => {
    // Active covers: printing, paused, slicing, uploading — verified by App.tsx.
    // Sidebar only receives the pre-computed count; verify count display accuracy.
    renderOnFleet(4, 0, 0);
    expect(screen.getByTestId('badge-active').textContent).toBe('4');
  });

  it('blocked is distinct from failed — blocked jobs stay in queue, failed are removed', () => {
    // A blocked job (filament mismatch / slice error at grab) stays visible with count 1.
    // A failed job (all retries exhausted) is stripped from the queue and never counted.
    renderOnFleet(0, 0, 1);
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
    // No active or pending badge — confirms blocked is its own category
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
  });
});

// ─── Settings sub-nav items ───────────────────────────────────────────────────

function renderOnSettings(collapsed = false) {
  return render(
    <MemoryRouter initialEntries={['/settings/tags']}>
      <Sidebar queueCounts={{ active: 0, pending: 0, blocked: 0 }}
               operatorName={null} printerCount={0} collapsed={collapsed} />
    </MemoryRouter>
  );
}

describe('Settings sub-nav items', () => {
  it('hides settings sub-items when not on a settings route', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.queryByText('Print defaults')).toBeNull();
    expect(screen.queryByText('Webhooks')).toBeNull();
  });

  it('shows settings sub-items when on /settings/* and not collapsed', () => {
    renderOnSettings(false);
    expect(screen.getByText('Tags')).toBeTruthy();
    expect(screen.getByText('Print defaults')).toBeTruthy();
    expect(screen.getByText('Maintenance')).toBeTruthy();
    expect(screen.getByText('Webhooks')).toBeTruthy();
    expect(screen.getByText('Filament inventory')).toBeTruthy();
    expect(screen.getByText('Plugins')).toBeTruthy();
  });

  it('hides settings sub-items when collapsed even on settings route', () => {
    renderOnSettings(true);
    expect(screen.queryByText('Print defaults')).toBeNull();
    expect(screen.queryByText('Webhooks')).toBeNull();
  });
});

// ─── Sidebar identity + live printer count ───────────────────────────────────

describe('Sidebar identity + printer count', () => {
  it('hides the identity row when operatorName is null', () => {
    const { container } = renderOnFleet(0, 0, 0, null, 3);
    expect(container.querySelector('.user-chip')).toBeNull();
  });

  it('still renders the printer count line when operatorName is null', () => {
    renderOnFleet(0, 0, 0, null, 3);
    expect(screen.getByText('3 printers')).toBeTruthy();
  });

  it('shows the identity row with single-word initials', () => {
    renderOnFleet(0, 0, 0, 'Maria', 1);
    expect(screen.getByText('Maria')).toBeTruthy();
    expect(screen.getByText('M')).toBeTruthy();
  });

  it('shows the identity row with two-word initials', () => {
    renderOnFleet(0, 0, 0, 'Maria Gomez', 1);
    expect(screen.getByText('Maria Gomez')).toBeTruthy();
    expect(screen.getByText('MG')).toBeTruthy();
  });

  it('uses singular "printer" for a count of 1', () => {
    renderOnFleet(0, 0, 0, null, 1);
    expect(screen.getByText('1 printer')).toBeTruthy();
  });

  it('uses plural "printers" for a count other than 1', () => {
    renderOnFleet(0, 0, 0, null, 0);
    expect(screen.getByText('0 printers')).toBeTruthy();
  });
});

// ─── Build info ───────────────────────────────────────────────────────────────

describe('Sidebar build info', () => {
  const SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678';

  function stubHealth(body: unknown) {
    vi.stubGlobal('fetch', vi.fn(async (url: string) =>
      new Response(JSON.stringify(url === '/api/v1/health' ? body : { enabled: false, url: null, has_api_key: false }),
        { status: 200 })));
  }

  it('shows the server version and short commit sha, full sha on hover', async () => {
    stubHealth({ status: 'ok', version: '0.1.0', git_sha: SHA });
    renderOnFleet(0, 0, 0);
    const el = await screen.findByTestId('build-info');
    expect(el.textContent).toBe('v0.1.0 · a1b2c3d');
    expect(el.getAttribute('title')).toBe(`Build ${SHA}`);
  });

  it('shows nothing when the health response lacks build info', async () => {
    stubHealth({ status: 'ok' });
    renderOnFleet(0, 0, 0);
    await screen.findByText('Fleet');
    expect(screen.queryByTestId('build-info')).toBeNull();
  });
});


// ─── Plugin navigation ────────────────────────────────────────────────────────

function withPlugins(plugins: ReturnType<typeof mkPlugin>[], extra: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/v1/plugins') return new Response(JSON.stringify({ plugins, slots: {} }), { status: 200 });
    return new Response(JSON.stringify(extra[url] ?? {}), { status: 200 });
  }));
}

describe('Plugin nav entries', () => {
  it('lists an enabled page plugin under settings, and nothing for a disabled one', async () => {
    withPlugins([mkPlugin({ id: 'spoolman', name: 'Spoolman', ui: { mode: 'page', nav_label: 'Spoolman', nav_placement: 'settings', nav_icon: null, tabs: [] } }),
                 mkPlugin({ id: 'other_plug', enabled: false, active: false, ui: { mode: 'page', nav_label: 'Other', nav_placement: 'settings', nav_icon: null, tabs: [] } })]);
    renderOnSettings(false);

    const link = await screen.findByRole('link', { name: 'Spoolman' });
    expect(link.getAttribute('href')).toBe('/plugins/spoolman');
    expect(screen.queryByRole('link', { name: 'Other' })).toBeNull();                     // disabled: no nav entry
  });

  it('a section plugin gets no sidebar entry of its own, and a main-placement page joins Workshop', async () => {
    withPlugins([mkPlugin({ id: 'small_one', ui: { mode: 'section', nav_label: 'Small', nav_placement: 'settings', nav_icon: null, tabs: [] } }),
                 mkPlugin({ id: 'ops_page', ui: { mode: 'page', nav_label: 'Ops board', nav_placement: 'main', nav_icon: null, tabs: [] } })]);
    renderOnFleet(0, 0, 0);

    const ops = await screen.findByRole('link', { name: 'Ops board' });
    expect(ops.getAttribute('href')).toBe('/plugins/ops_page');
    expect(screen.queryByRole('link', { name: 'Small' })).toBeNull();
  });

  it('keeps the settings list open on a plugin page', async () => {
    withPlugins([mkPlugin({ id: 'spoolman', ui: { mode: 'page', nav_label: 'Spoolman', nav_placement: 'settings', nav_icon: null, tabs: [] } })]);
    render(<MemoryRouter initialEntries={['/plugins/spoolman/connection']}>
      <Sidebar queueCounts={{ active: 0, pending: 0, blocked: 0 }} operatorName={null} printerCount={0} />
    </MemoryRouter>);
    expect(await screen.findByRole('link', { name: 'Spoolman' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'API Keys' })).toBeTruthy();
  });
});

describe('Inventory status chip in the sidebar', () => {
  it('shows nothing without an active provider', async () => {
    withPlugins([]);
    renderOnFleet(0, 0, 0);
    await screen.findByText('Settings');
    expect(screen.queryByTestId('inventory-chip')).toBeNull();
  });

  it('shows the provider with its sync tone once one is active', async () => {
    const plugin = mkPlugin({ id: 'spoolman', name: 'Spoolman' });
    withPlugins([plugin], { '/api/v1/inventory/sync-status': mkStatus({ provider: 'spoolman', capabilities: plugin.capabilities, pending_count: 2 }) });
    renderOnFleet(0, 0, 0);

    const chip = await screen.findByTestId('inventory-chip');
    expect(chip.getAttribute('data-tone')).toBe('success');
    expect(chip.textContent).toContain('Spoolman');
    expect(chip.textContent).toContain('2 queued');
  });

  it('turns red while a remote provider is unreachable', async () => {
    const plugin = mkPlugin({ id: 'spoolman', name: 'Spoolman' });
    withPlugins([plugin], { '/api/v1/inventory/sync-status': mkStatus({ disconnected_since: '2026-01-01T00:00:00Z', last_error: 'refused' }) });
    renderOnFleet(0, 0, 0);
    expect((await screen.findByTestId('inventory-chip')).getAttribute('data-tone')).toBe('disconnected');
  });

  it('a provider that is not remote is simply "ready" (no sync health to show)', async () => {
    const plugin = mkPlugin({ id: 'local_one', name: 'Local', capabilities: ['TRACKS_WEIGHT'] });
    withPlugins([plugin], { '/api/v1/inventory/sync-status': mkStatus({ last_sync_at: null }) });   // never "synced": not a failure here
    renderOnFleet(0, 0, 0);
    const chip = await screen.findByTestId('inventory-chip');
    expect(chip.getAttribute('data-tone')).toBe('success');
    expect(chip.textContent).toContain('ready');
  });
});


describe('Filament library nav entry', () => {
  it.each([
    ['a provider that owns its library', ['MANAGE_SPOOLS'], true],
    ['a provider that owns materials only', ['MANAGE_MATERIALS'], true],
    ['a provider whose library lives elsewhere', ['TRACKS_WEIGHT', 'REMOTE'], false],
  ])('%s', async (_n, capabilities, shown) => {
    withPlugins([mkPlugin({ id: 'any_provider', capabilities })]);
    renderOnFleet(0, 0, 0);
    await screen.findByText('Settings');
    await waitFor(() => expect(!!screen.queryByRole('link', { name: 'Filament library' })).toBe(shown));
    if (shown) expect(screen.getByRole('link', { name: 'Filament library' }).getAttribute('href')).toBe('/library');
  });

  it('is absent with no provider', async () => {
    withPlugins([]);
    renderOnFleet(0, 0, 0);
    await screen.findByText('Settings');
    expect(screen.queryByRole('link', { name: 'Filament library' })).toBeNull();
  });
});
