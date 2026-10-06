import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

const SPOOLMAN = {
  id: 'spoolman', name: 'Spoolman', kind: 'filament_inventory', version: '1', description: 'Filament tracker', docs_url: null,
  source: 'bundled', capabilities: ['TRACKS_WEIGHT', 'WRITE_WEIGHT', 'REMOTE', 'LABEL_SCAN', 'PROFILE_LINKS_READ', 'PROFILE_LINKS_WRITE'],
  enabled: true, active: true, error: null,
  ui: { mode: 'page', nav_label: 'Spoolman', nav_placement: 'settings', nav_icon: null,
        tabs: [{ id: 'connection', label: 'Connection', renderer: 'default' }, { id: 'mappings', label: 'Filament mappings', renderer: 'component' }] },
};
const LOCAL = { ...SPOOLMAN, id: 'local_inv', name: 'Local', enabled: false, active: false, capabilities: ['TRACKS_WEIGHT'],
  ui: { mode: 'page', nav_label: 'Local', nav_placement: 'settings', nav_icon: null, tabs: [] } };
const PLUGINS = { plugins: [SPOOLMAN, LOCAL], slots: { filament_inventory: 'spoolman' } };
const DETAIL = {
  ...SPOOLMAN, settings: { url: 'http://spoolman.test', sync_interval_minutes: 15, max_disconnect_minutes: null }, secrets: { api_key: false },
  secret_fields: ['api_key'], state: {},
  settings_schema: { properties: { url: { type: 'string', title: 'Url' }, api_key: { type: 'string', title: 'Api Key' },
                                   sync_interval_minutes: { type: 'integer', title: 'Sync interval' } }, required: ['url'] },
};

test.describe('Plugins', () => {
  test('the old Spoolman settings URL lands on the plugin page, whose form is built from its schema', async ({ page }) => {
    await mockApi(page, { plugins: PLUGINS, respond: () => undefined });
    await page.route('**/api/v1/plugins/spoolman', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DETAIL) }));
    await page.goto('/settings/spoolman');

    await expect(page).toHaveURL(/\/plugins\/spoolman\/connection$/);
    await expect(page.getByRole('heading', { name: 'Spoolman' })).toBeVisible();
    await expect(page.getByLabel('Url')).toHaveValue('http://spoolman.test');
    await expect(page.getByLabel('Api Key')).toHaveAttribute('placeholder', 'Not set');
    await expect(page.getByRole('link', { name: 'Filament mappings' })).toBeVisible();     // the component tab
  });

  test('Settings → Filament inventory lists the providers; Plugins lists them with their state', async ({ page }) => {
    await mockApi(page, { plugins: PLUGINS });
    await page.route('**/api/v1/inventory/settings', route => route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ provider: 'spoolman', deduct_on_complete: true, low_stock: { default_g: null, overrides: {} } }) }));
    await page.goto('/settings/inventory');

    const picker = page.getByLabel('Inventory provider');
    await expect(picker).toHaveValue('spoolman');
    await expect(picker.locator('option')).toHaveText(['None', 'Spoolman', 'Local']);
    await expect(page.getByText('Deduct filament when a job completes')).toBeVisible();

    await page.goto('/settings/plugins');
    await expect(page.getByTestId('plugin-spoolman')).toContainText('Active');
    await expect(page.getByTestId('plugin-local_inv')).toContainText('Disabled');
  });

  test('with no provider the app offers no spool features', async ({ page }) => {
    await mockApi(page);
    await page.goto('/fleet');
    await expect(page.getByText(/\d+\s+printers/).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /scan spool/i })).toHaveCount(0);
    await expect(page.getByTestId('inventory-chip')).toHaveCount(0);
  });
});
