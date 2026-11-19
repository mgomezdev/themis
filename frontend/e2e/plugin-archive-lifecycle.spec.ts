import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { mockApi } from './mock-api';
import { mkPlugin } from '../src/test/inventoryFixtures';

const archive = fileURLToPath(new URL('./fixtures/inventory-plugin.zip', import.meta.url));

for (const removeData of [false, true]) {
  test(`archive install and uninstall clear navigation and routes (remove data: ${removeData})`, async ({ page }) => {
    await mockApi(page);
    let staged = false, loaded = false, removing = false, gone = false, down = false;
    let uploadType = '', uploadBody = '', restartCount = 0;
    const deletes: string[] = [];
    const plugin = mkPlugin({ id: 'acme_inv', name: 'Acme inventory', source: 'upload',
      ui: { mode: 'page', nav_label: 'Acme inventory', nav_placement: 'settings', nav_icon: null,
        tabs: [{ id: 'settings', label: 'Acme settings', renderer: 'default' }, { id: 'extra', label: 'Acme extra', renderer: 'default' }] } });
    const summary = () => ({ ...plugin, loaded, enabled: !removing, active: loaded && !removing,
      install: { status: removing ? 'pending_removal' : loaded ? 'active' : 'pending_restart', version: '1.0.0',
        previous_version: null, publisher: 'Acme', source_url: 'inventory-plugin.zip', ref: null, commit_sha: null,
        archive_sha256: 'ab'.repeat(32), installed_at: '2026-01-01T00:00:00Z', error: null, can_rollback: false, can_check_updates: false } });
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/v1', '');
      const ok = (data: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      if (path === '/plugins') return ok({ plugins: staged && !gone ? [summary()] : [], selections: {},
        pending: staged && (!loaded || removing) && !gone ? [{ plugin_id: plugin.id, name: plugin.name, version: '1.0.0', change: removing ? 'uninstall' : 'install' }] : [] });
      if (path === '/plugins/install') {
        expect(req.method()).toBe('POST');
        expect(url.searchParams.get('preview')).toBe('true');
        uploadType = req.headers()['content-type']; uploadBody = req.postDataBuffer()!.toString('latin1');
        return ok({ preview: { token: 'archive-token', id: plugin.id, name: plugin.name, version: '1.0.0',
          provides: [], requires: [], optional: [], defines: [], publisher: 'Acme', description: 'Fixture plugin',
          source: 'upload', source_url: 'inventory-plugin.zip', ref: null, commit_sha: null,
          archive_sha256: 'ab'.repeat(32), min_themis: null } });
      }
      if (path === '/plugins/install/archive-token/commit') { staged = true; return ok({ id: plugin.id, status: 'pending_restart' }); }
      if (path === `/plugins/${plugin.id}`) {
        if (req.method() === 'DELETE') { deletes.push(url.searchParams.get('remove_data')!); removing = true; return ok({ status: 'pending_removal' }); }
        if (gone) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Unknown plugin"}' });
        return ok({ ...summary(), settings: { endpoint: 'http://fixture.invalid' }, secrets: {}, secret_fields: [], state: {},
          settings_schema: { properties: { endpoint: { type: 'string', title: 'Acme endpoint' } } } });
      }
      if (path === '/system/restart') {
        if (req.method() === 'GET') return ok({ pending: [], printing: [] });
        expect(req.postDataJSON()).toEqual({ force: false });
        restartCount++; loaded = true; gone = removing; down = true;
        return ok({ restarting: true });
      }
      if (path === '/health') { if (down) { down = false; return route.abort('connectionrefused'); } return ok({ status: 'ok' }); }
      return route.fallback();
    });
    await page.goto('/settings/plugins');
    await page.getByRole('button', { name: 'Install plugin' }).click();
    await page.getByLabel('Plugin archive').setInputFiles(archive);
    await page.getByRole('button', { name: 'Review' }).click();
    const preview = page.getByTestId('install-preview');
    await expect(preview).toContainText('inventory-plugin.zip');
    await expect(preview).toContainText('ab'.repeat(32));
    await expect(page.getByRole('alert').filter({ hasText: 'full access to Themis' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Install', exact: true })).toBeDisabled();
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Install', exact: true }).click();
    expect(uploadType).toContain('multipart/form-data; boundary=');
    expect(uploadBody).toContain('filename="inventory-plugin.zip"');
    // Chromium omits file bytes from request.postData(); backend archive validation has its own tests.
    await expect(page.getByTestId('restart-banner')).toHaveCount(1);
    await expect(page.getByTestId('restart-banner')).toContainText('1 pending change');
    expect(restartCount).toBe(0);
    await page.getByRole('button', { name: 'Restart Themis' }).click();
    await page.getByRole('button', { name: 'Restart now' }).click();
    await expect(page.getByTestId('restart-banner')).toHaveCount(0, { timeout: 15000 });
    await page.getByRole('link', { name: 'Acme inventory', exact: true }).click();
    await expect(page.getByRole('navigation', { name: 'Acme inventory tabs' })).toBeVisible();
    await expect(page.getByLabel('Acme endpoint')).toHaveValue('http://fixture.invalid');
    await page.goto('/settings/plugins');
    await page.getByRole('button', { name: 'Uninstall', exact: true }).click();
    const confirm = page.getByTestId('uninstall-confirm-acme_inv');
    await expect(confirm.getByRole('checkbox')).not.toBeChecked();
    if (removeData) await confirm.getByRole('checkbox').check();
    await confirm.getByRole('button', { name: 'Uninstall', exact: true }).click();
    await expect(confirm).toHaveCount(0);
    expect(deletes).toEqual([String(removeData)]);
    await expect(page.getByRole('link', { name: 'Acme inventory', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Restart Themis' }).click();
    await page.getByRole('button', { name: 'Restart now' }).click();
    await expect(page.getByTestId('restart-banner')).toHaveCount(0, { timeout: 15000 });
    expect(restartCount).toBe(2);
    for (const tab of ['settings', 'extra']) {
      await page.goto(`/plugins/acme_inv/${tab}`);
      await expect(page.getByRole('link', { name: 'Acme inventory', exact: true })).toHaveCount(0);
      await expect(page.getByRole('navigation', { name: 'Acme inventory tabs' })).toHaveCount(0);
      await expect(page.getByLabel('Acme endpoint')).toHaveCount(0);
      await expect(page.getByText('No plugin “acme_inv” is installed.')).toBeVisible();
    }
  });
}
