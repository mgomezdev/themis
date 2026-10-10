import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

/**
 * BIZ-250 acceptance: the Capabilities page offers only providers that can serve as a choose-one default, shows a dormant default
 * (and why) instead of replacing it, and lists every provider of a fan-out capability instead of a single-choice dropdown.
 */

const prov = (plugin_id: string, name: string, over: Record<string, unknown> = {}) =>
  ({ plugin_id, name, version: 1, enabled: true, status: 'serving', waiting_on: [], ...over });
const cap = (over: Record<string, unknown>) => ({
  version: 1, description: '', definer: null, features: [], required_methods: [], explicit: true, waiting_on: [], error: null,
  requires_by: [], mode: 'exclusive', dormant_default: null, ...over,
});

test('choose-one shows a dormant default and its reason; fan-out lists every provider', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/v1/capabilities', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ capabilities: [
    cap({ id: 'acme.slicing', label: 'Slicing', mode: 'choose_one', selected: 'gone', status: 'no_provider',
          dormant_default: { plugin_id: 'gone', reason: 'plugin_disabled' },
          providers: [prov('laminus', 'Laminus'), prov('gone', 'Gone slicer', { enabled: false, status: 'disabled' }), prov('off', 'Off slicer', { enabled: false, status: 'disabled' })] }),
    cap({ id: 'acme.notify', label: 'Notifications', mode: 'fan_out', selected: null, status: 'serving',
          providers: [prov('ntfy', 'ntfy'), prov('mail', 'Email', { enabled: false, status: 'disabled' })] }),
  ] }) }));
  await page.goto('/settings/capabilities');

  const select = page.getByLabel('Slicing default provider');
  await expect(select).toHaveValue('gone');
  await expect(select.locator('option')).toHaveText(['None', 'Laminus', 'Gone slicer (unavailable)']);
  await expect(page.getByRole('alert')).toContainText('its plugin is disabled');
  await expect(page.getByRole('alert')).toContainText('never picks one for you');

  const list = page.getByRole('list', { name: 'Notifications providers' });
  await expect(list).toContainText('ntfy — serving');
  await expect(list).toContainText('Email — disabled');
  await expect(page.getByLabel('Notifications provider', { exact: true })).toHaveCount(0);
});
