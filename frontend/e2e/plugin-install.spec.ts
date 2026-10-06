import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

const json = (route: any, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

const PREVIEW = {
  token: 'tok1', id: 'acme_inv', name: 'Acme inventory', version: '1.0.0', kind: 'filament_inventory', publisher: 'Acme',
  description: 'Acme stock', source: 'github', source_url: 'https://github.com/acme/inv', ref: 'main',
  commit_sha: 'c0ffee'.padEnd(40, '0'), archive_sha256: 'ab'.repeat(32), min_themis: null,
};

test.describe('Plugin installation', () => {
  test('install from GitHub: review + trust warning, one stacked restart banner, restart after the printer check', async ({ page }) => {
    let committed = false;
    let restartBody: unknown = null;
    await mockApi(page, {});
    await page.route('**/api/v1/plugins', route => json(route, {
      plugins: [], slots: { filament_inventory: null },
      pending: committed ? [{ plugin_id: 'acme_inv', name: 'Acme inventory', version: '1.0.0', change: 'install' }] : [],
    }));
    await page.route('**/api/v1/plugins/install-from-github', route => json(route, { preview: PREVIEW }));
    await page.route('**/api/v1/plugins/install/tok1/commit', route => { committed = true; return json(route, { id: 'acme_inv', status: 'pending_restart' }); });
    await page.route('**/api/v1/system/restart', route => {
      if (route.request().method() === 'GET') return json(route, { pending: [], printing: [] });
      restartBody = route.request().postDataJSON();
      return json(route, { restarting: true });
    });

    await page.goto('/settings/plugins');
    await expect(page.getByTestId('restart-banner')).toHaveCount(0);
    await page.getByRole('button', { name: 'Install plugin' }).click();
    await page.getByRole('button', { name: 'GitHub repository' }).click();
    await page.getByLabel('Repository URL').fill('https://github.com/acme/inv');
    await page.getByRole('button', { name: 'Review' }).click();

    const preview = page.getByTestId('install-preview');
    await expect(preview).toContainText('Acme inventory');
    await expect(preview).toContainText('https://github.com/acme/inv @ main');
    await expect(preview).toContainText('ab'.repeat(32));
    await expect(page.getByRole('alert').filter({ hasText: 'full access to Themis' })).toBeVisible();
    const install = page.getByRole('button', { name: 'Install', exact: true });
    await expect(install).toBeDisabled();                                                  // the warning must be accepted
    await page.getByRole('checkbox').check();
    await install.click();

    await expect(page.getByRole('dialog')).toHaveCount(0);
    const banner = page.getByTestId('restart-banner');
    await expect(banner).toContainText('Restart Themis to apply 1 pending change');
    expect(restartBody).toBeNull();                                                         // never automatic
    await page.getByRole('button', { name: 'Restart Themis' }).click();
    await page.getByRole('button', { name: 'Restart now' }).click();
    await expect(banner).toContainText('Restarting Themis');
    expect(restartBody).toEqual({ force: false });
  });
});
