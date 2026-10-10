import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

/**
 * BIZ-263 acceptance: a legacy pre-sliced G-code file shows as "unknown" machine eligibility (never a match); the user ticks the
 * printer models it is for in the file detail drawer and Themis stores exactly that set.
 */

const GCODE = {
  id: 7, original_filename: 'bracket.gcode', relative_path: 'bracket.gcode', folder: '/', size_bytes: 100, plate_count: 1,
  uploaded_at: '2026-01-01T00:00:00Z', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [], kind: 'gcode',
  sliced_version_count: 0, sliced_version: null, eligibility: { known: false, model_uuids: [] },
};
const model = (id: string, name: string) => ({
  id, plugin_id: 'acme', manufacturer_id: 'acme', manufacturer_name: 'Acme', model_id: id, display_name: name, bed_mm: [256, 256],
  toolheads: 1, enabled: true, dormant: false, dormant_reason: null, printer_count: 0,
});

test('an unknown G-code file is flagged and the user records which printer models it is for', async ({ page }) => {
  await mockApi(page, { files: [GCODE] });
  const puts: unknown[] = [];
  let known = false;
  const tree = { name: '', path: '/', count: 1, children: {} };
  await page.route(/\/api\/v1\/files\/(tree|dirs)$/, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tree) }));
  await page.route('**/api/v1/tags', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route('**/api/v1/printer-models**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify([model('x1', 'X1'), model('x2', 'X2')]) }));
  await page.route('**/api/v1/files/7/eligibility', async route => {
    const req = route.request();
    if (req.method() === 'PUT') {
      const { model_uuids } = req.postDataJSON();
      puts.push(model_uuids);
      known = true;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        known: true, models: model_uuids.map((u: string) => ({ model_uuid: u, source: 'manual', display_name: u.toUpperCase(), manufacturer_name: 'Acme' })) }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ known, models: [] }) });
  });
  await page.goto('/files');

  await page.getByText('bracket.gcode').first().click();
  await expect(page.getByTestId('file-eligibility').getByRole('status')).toContainText('Unknown');

  await page.getByLabel('Acme X2').check();
  await page.getByRole('button', { name: 'Save eligibility' }).click();

  await expect.poll(() => puts).toEqual([['x2']]);
  await expect(page.getByTestId('file-eligibility').getByRole('status')).toHaveCount(0);
});
