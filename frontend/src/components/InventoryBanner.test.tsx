import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InventoryBanner } from './InventoryBanner';
import { stubFetch } from '../test/fetchStub';
import { mkPlugin, mkStatus, pluginsBody } from '../test/inventoryFixtures';
import { resetPluginStore } from '../api/plugins';

const boot = (plugin: ReturnType<typeof mkPlugin> | null, status: ReturnType<typeof mkStatus>) => {
  stubFetch({ 'GET /api/v1/plugins': pluginsBody(plugin), 'GET /api/v1/inventory/sync-status': status });
  render(<MemoryRouter><InventoryBanner /></MemoryRouter>);
};

describe('InventoryBanner', () => {
  beforeEach(() => resetPluginStore());
  afterEach(() => { vi.unstubAllGlobals(); resetPluginStore(); });

  it('warns that data is last-known, how old, and how many weight updates are queued, while unreachable', async () => {
    boot(mkPlugin({ id: 'sm', name: 'Spoolman' }), mkStatus({ disconnected_since: '2026-01-01T00:00:00Z', cache_as_of: '2026-01-01T00:00:00Z', pending_count: 3 }));
    const banner = await screen.findByTestId('inventory-banner');
    expect(banner.textContent).toContain('Spoolman is unreachable.');
    expect(banner.textContent).toContain('last-known copy (as of');
    expect(banner.textContent).toContain('3 weight updates are queued');
    expect(screen.getByRole('link', { name: 'Details' }).getAttribute('href')).toBe('/plugins/sm');
  });

  it('also shows for a failing sync, with the singular wording for one queued update', async () => {
    boot(mkPlugin({ id: 'sm', name: 'Spoolman' }), mkStatus({ last_error: 'HTTP 500', pending_count: 1 }));
    expect((await screen.findByTestId('inventory-banner')).textContent).toContain('1 weight update is queued');
  });

  it.each([
    ['healthy', mkPlugin({ id: 'sm' }), mkStatus()],
    ['no provider', null, mkStatus()],
    ['a provider that is not remote', mkPlugin({ id: 'loc', capabilities: ['TRACKS_WEIGHT'] }), mkStatus({ disconnected_since: '2026-01-01T00:00:00Z' })],
  ])('shows nothing when %s', async (_name, plugin, status) => {
    boot(plugin, status);
    await new Promise(r => setTimeout(r, 30));
    expect(screen.queryByTestId('inventory-banner')).toBeNull();
  });
});
