import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';
import { mkPlugin, mkSpool, mkStatus } from '../src/test/inventoryFixtures';

// BIZ-309: browser recovery using isolated provider/outbox fixtures.
test('cached inventory, outage alert, queued writes and suspended tracking recover through the UI', async ({ page }) => {
  const plugin = mkPlugin({ id: 'fixture_inventory', name: 'Fixture inventory' });
  await mockApi(page, { plugins: { plugins: [plugin], selections: { 'inventory.filament': plugin.id } } });
  const cached = '2026-01-01T09:00:00Z';
  let writes = [11, 12].map(id => ({ id, provider: plugin.id, spool_ref: String(id), target_g: 420,
    job_id: 70 + id, printer_id: 1, source: 'queue', created_at: cached, attempts: 2,
    last_attempt_at: cached, last_error: 'Provider unreachable', status: 'pending', pre_weight_g: 500, conflict_current_g: null }));
  let suspended = [{ spool_ref: '13', reason: 'missing_snapshot', since: cached, job_id: 90 }];
  const mutations: { path: string; body: unknown }[] = [];
  const spoolReads: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace('/api/v1', '');
    const ok = (data: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    if (path === `/plugins/${plugin.id}`) return ok({ ...plugin, settings: {}, secrets: {}, secret_fields: [], settings_schema: { properties: {} }, state: {} });
    if (path === '/inventory/sync-status') return ok(mkStatus({ provider: plugin.id, capabilities: plugin.provides[0].features,
      disconnected_since: '2026-01-01T09:01:00Z', max_disconnect_minutes: 30, disconnect_alerted: true,
      last_sync_at: cached, cache_as_of: cached, last_error: 'Connection refused', last_error_code: 'ConnectError', pending_count: writes.length }));
    if (path === '/inventory/spools') { spoolReads.push(path); return ok({ provider: plugin.id, stale: true, as_of: cached, items: [mkSpool('11', { material: { name: 'Cached red spool' } })] }); }
    if (path === '/inventory/pending-writes') return ok({ provider: plugin.id, items: writes });
    if (path === '/inventory/tracking') return ok({ provider: plugin.id, items: suspended });
    if (req.method() === 'POST' && path.startsWith('/inventory/')) {
      mutations.push({ path, body: req.postData() ? req.postDataJSON() : null });
      if (path === '/inventory/pending-writes/11/resolve') { writes = writes.filter(w => w.id !== 11); return ok({ status: 'applied' }); }
      if (path === '/inventory/pending-writes/12/discard') { writes = writes.filter(w => w.id !== 12); return ok({ status: 'discarded' }); }
      if (path === '/inventory/spools/13/resume-tracking') { suspended = []; return ok({ tracking: 'active' }); }
      return route.fulfill({ status: 500, body: 'Unexpected inventory mutation' });
    }
    return route.fallback();
  });
  await page.goto('/fleet');
  await page.locator('.card').filter({ hasText: 'U1' }).first().click({ position: { x: 100, y: 20 } });
  await page.getByRole('button', { name: 'Change' }).click();
  await page.getByPlaceholder('Search spools…').first().focus();
  await expect(page.getByText(/#11 Cached red spool/)).toBeVisible();
  expect(spoolReads.length).toBeGreaterThan(0);
  const banner = page.getByTestId('inventory-banner');
  await expect(banner).toContainText('Fixture inventory is unreachable');
  await expect(banner).toContainText('last-known copy (as of');
  await expect(banner).toContainText('2 weight updates are queued');
  await banner.getByRole('link', { name: 'Details' }).click();
  await expect(page.getByTestId('sync-tone')).toHaveText('Unreachable');
  await expect(page.getByTestId('outage')).toContainText('alert after 30 min — sent');
  await expect(page.getByText('Last-known data cached', { exact: false })).toBeVisible();
  const pending = page.getByTestId('pending-11');
  await expect(pending).toContainText('Spool #11 → 420 g');
  await expect(pending).toContainText('2 attempts');
  await pending.getByRole('button', { name: 'Apply now' }).click();
  await expect(pending).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveText('Applied');
  await page.getByRole('button', { name: 'Discard update for spool 12' }).click();
  await expect(page.getByTestId('pending-12')).toHaveCount(0);
  await expect(page.getByTestId('pending-writes')).toContainText('every deduction has reached the provider');
  await expect(page.getByTestId('suspended-13')).toContainText('missing_snapshot');
  await page.getByLabel('Corrected weight for spool 13').fill('375');
  await page.getByRole('button', { name: 'Set weight & resume' }).click();
  await expect(page.getByTestId('suspended')).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveText('Tracking resumed');
  expect(mutations).toEqual([
    { path: '/inventory/pending-writes/11/resolve', body: {} },
    { path: '/inventory/pending-writes/12/discard', body: null },
    { path: '/inventory/spools/13/resume-tracking', body: { remaining_g: 375 } },
  ]);
});
