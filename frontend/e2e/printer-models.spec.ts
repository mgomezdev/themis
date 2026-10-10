import { test, expect, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

/**
 * BIZ-262 acceptance: the printer-model registry's enabled subset drives the add-printer flow. Disabling a model on the
 * Printers screen removes it from the wizard's model picker; a stateful fake of /printer-models + /printers/types stands in
 * for the backend.
 */

const model = (id: string, name: string, enabled = true) => ({
  id: `uuid-${id}`, plugin_id: 'acme', manufacturer_id: 'acme', manufacturer_name: 'Acme', model_id: id, display_name: name,
  bed_mm: [256, 256], toolheads: 1, enabled, dormant: false, dormant_reason: null, printer_count: 0,
});

async function fakeRegistry(page: Page) {
  const models = [model('x1', 'X1'), model('x1_pro', 'X1 Pro')];
  const patches: { id: string; enabled: boolean }[] = [];
  await page.route('**/api/v1/printer-models**', async route => {
    const req = route.request();
    const id = new URL(req.url()).pathname.split('/').pop()!;
    if (req.method() === 'PATCH') {
      const { enabled } = req.postDataJSON();
      patches.push({ id, enabled });
      const m = models.find(x => x.id === id)!;
      m.enabled = enabled;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(m) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(models) });
  });
  await page.route('**/api/v1/printers/types', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify(models.map(m => ({
      plugin_id: m.plugin_id, manufacturer_id: m.manufacturer_id, manufacturer_name: m.manufacturer_name, model_id: m.model_id,
      display_name: m.display_name, bed_mm: m.bed_mm, toolheads: 1, connection_fields: [], plugin_enabled: true,
      model_uuid: m.id, model_enabled: m.enabled,
    }))),
  }));
  return patches;
}

test('disabling a model in the registry removes it from the add-printer model picker', async ({ page }) => {
  await mockApi(page, { printers: [] });
  const patches = await fakeRegistry(page);
  await page.goto('/settings/printer-models');

  await expect(page.getByLabel('Acme X1 Pro')).toBeChecked();
  await page.getByLabel('Acme X1 Pro').click();
  await expect(page.getByLabel('Acme X1 Pro')).not.toBeChecked();
  await expect.poll(() => patches).toEqual([{ id: 'uuid-x1_pro', enabled: false }]);

  await page.goto('/fleet');
  await page.getByText('Add printer').first().click();
  const options = await page.getByLabel('Model').locator('option').allTextContents();
  expect(options).toEqual(['X1']);
});
