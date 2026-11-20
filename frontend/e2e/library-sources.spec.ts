import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('generic sources sync metadata, show errors and retain catalog browsing when disabled', async ({ page }) => {
  await mockApi(page, { files: [] });
  let synced = false, active = true, attempts = 0;
  const posts: string[] = [];
  await page.route('**/api/v1/library/**', route => {
    const path = new URL(route.request().url()).pathname;
    const ok = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/sources')) return ok({ sources: [{ plugin_id: 'fixture_source', name: 'Fixture source', enabled: active, active, error: null, last_sync_at: null }] });
    if (route.request().method() === 'POST') {
      posts.push(path); attempts++;
      if (attempts === 1) return ok({ detail: 'Source is temporarily unavailable' }, 502);
      synced = true;
      return ok({ entries: 1, full_scan: true });
    }
    return ok({ total: synced ? 1 : 0, items: synced ? [{ id: 'entry-one', provider_plugin_id: 'fixture_source', name: 'Remote part.stl',
      source_url: 'https://source.test/models/one', missing: false, cached_file_id: null, metadata: { creator: { name: 'Artist' }, license_code: null } }] : [] });
  });
  await page.goto('/files');
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByText('Fixture source', { exact: true })).toBeVisible();
  await expect(page.getByText('No synchronized files yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Sync now' }).click();
  await expect(page.getByRole('alert')).toHaveText('Source is temporarily unavailable');
  await page.getByRole('button', { name: 'Sync now' }).click();
  await expect(page.getByRole('status')).toHaveText('Synchronized 1 catalog entries.');
  const entry = page.getByTestId('catalog-entry-one');
  await expect(entry).toContainText('Artist');
  await expect(entry).toContainText('Unknown');
  await expect(entry).toContainText('Metadata only');
  await expect(entry.getByRole('link', { name: 'Remote part.stl' })).toHaveAttribute('href', 'https://source.test/models/one');
  expect(posts).toEqual(['/api/v1/library/sources/fixture_source/sync', '/api/v1/library/sources/fixture_source/sync']);
  active = false;
  await page.reload();
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sync now' })).toBeDisabled();
  await expect(entry).toBeVisible();
});

test('catalog pages remain browsable beyond the first hundred entries', async ({ page }) => {
  await mockApi(page, { files: [] });
  await page.route('**/api/v1/library/**', route => {
    const url = new URL(route.request().url());
    const body = url.pathname.endsWith('/sources') ? { sources: [] } : {
      total: 101, items: url.searchParams.get('offset') === '100'
        ? [{ id: 'last', name: 'Last part.stl', provider_plugin_id: 'old_source', source_url: 'https://source.test/last', missing: true, cached_file_id: null, metadata: {} }]
        : Array.from({ length: 100 }, (_, i) => ({ id: String(i), name: `Part ${i}.stl`, provider_plugin_id: 'old_source', source_url: `https://source.test/${i}`, missing: false, cached_file_id: null, metadata: {} })),
    };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/files');
  await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByTestId('catalog-0')).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByTestId('catalog-last')).toContainText('Missing at source');
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(page.getByTestId('catalog-0')).toBeVisible();
});
