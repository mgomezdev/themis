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

test('a model without a slicer profile is flagged and its profile is confirmed once from the provider\'s suggestion', async ({ page }) => {
  // a complete plugin summary: the sidebar reads `ui.mode` / `ui.nav_placement` of every plugin, so a partial one blanks the page
  const laminus = {
    id: 'laminus', name: 'Laminus', version: '1', description: 'Slicer', docs_url: null, source: 'bundled', requires: [], optional: [], defines: [],
    provides: [{ capability: 'gcode.produce', version: 1, selected: true, status: 'serving', waiting_on: [], features: [] }],
    enabled: true, active: true, error: null, loaded: true, install: null,
    ui: { mode: 'section', nav_label: 'Laminus', nav_placement: 'settings', nav_icon: null, tabs: [] },
  };
  const plugins = { plugins: [laminus], selections: { 'gcode.produce': 'laminus' } };
  await mockApi(page, { printers: [], plugins });
  await fakeRegistry(page);
  const rows: any[] = [];
  const posts: any[] = [];
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/v1/printer-models/*/slicer-profiles**', async route => {
    const req = route.request();
    if (req.url().includes('/suggestions')) {
      return route.fulfill(json({ provider_plugin_id: 'laminus', available: true, error: null, variants: [], candidates: [
        { name: 'Acme X1 0.4 nozzle', nozzle_diameter_mm: 0.4, flow: 'standard', ref: 'machine:acme-x1-0.4-nozzle', confidence: 0.97, match_reason: 'exact_model' }] }));
    }
    if (req.method() === 'POST') {
      const body = req.postDataJSON();
      posts.push(body);
      const row = { id: rows.length + 1, model_uuid: 'uuid-x1', provider_plugin_id: body.provider_plugin_id, nozzle_diameter_mm: body.nozzle_diameter_mm,
                    flow: body.flow, machine_profile: body.machine_profile, is_default: body.is_default };
      rows.push(row);
      return route.fulfill(json(row, 201));
    }
    return route.fulfill(json(rows));
  });
  await page.goto('/settings/printer-models');

  await expect(page.getByText('no slicer profile').first()).toBeVisible();
  await page.getByRole('button', { name: 'Slicer profile' }).first().click();
  await page.getByRole('radio', { name: /Acme X1 0\.4 nozzle/ }).check();
  await page.getByRole('button', { name: 'Confirm profile' }).click();

  await expect(page.getByTestId('msp-row-1')).toBeVisible();
  expect(posts).toEqual([{ provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow: 'standard', machine_profile: 'Acme X1 0.4 nozzle', is_default: true }]);
});
