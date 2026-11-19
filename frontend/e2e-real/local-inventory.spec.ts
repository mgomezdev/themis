import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';

// BIZ-307: real production frontend + FastAPI + SQLite. No route interception.
test('Local inventory lifecycle, Fleet binding, tuple matching and disabled navigation', async ({ page, request }) => {
  const key = 'isolated-browser-test-key';
  await page.addInitScript(k => localStorage.setItem('themis.apiKey', k), key);
  const api = async (method: string, path: string, data?: unknown) => {
    const res = await request.fetch(`/api/v1${path}`, { method, headers: { 'X-Api-Key': key }, data });
    expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy();
    return res.json();
  };
  // Explicitly declare required plugins and provider selection; no shipped-default assumptions.
  await api('PUT', '/plugins/mock', { enabled: true });
  // A configured but unreachable loopback URL builds the provider without requiring Spoolman.
  await api('PUT', '/plugins/spoolman', { enabled: true, settings: { url: 'http://127.0.0.1:9' } });
  await api('PUT', '/plugins/local_inventory', { enabled: false });
  await api('PUT', '/capabilities/inventory.filament/provider', { plugin_id: 'spoolman' });
  const printer = await api('POST', '/printers', { name: 'Inventory acceptance printer', printer_type: 'mock', connection_config: {},
    loaded_filaments: [{ slot: 0, type: 'PLA', subtype: 'standard', color: '#FFFFFF' }] });
  // Keep matching observable without dispatching the fixture job.
  await api('PATCH', `/printers/${printer.id}`, { queue_on: false });

  await page.goto('/settings/inventory');
  await expect(page.getByLabel('Inventory provider')).toHaveValue('spoolman');
  await page.getByLabel('Inventory provider').selectOption('local_inventory');
  await expect(page.getByLabel('Inventory provider')).toHaveValue('local_inventory');
  await page.getByRole('link', { name: 'Spoolman', exact: true }).click();
  await page.getByRole('switch').first().click();
  await expect(page.getByTestId('plugin-disabled')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Spoolman', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Filament library', exact: true }).click();
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  await page.getByRole('button', { name: 'Add material', exact: true }).click();
  await page.getByLabel('Material name').fill('Acceptance red');
  await page.getByLabel('Material type').fill('PLA');
  await page.getByLabel('Material subtype').fill('silk');
  await page.getByLabel('Vendor', { exact: true }).fill('Fixture vendor');
  await page.getByLabel('Colour', { exact: true }).fill('#FF0000');
  await page.getByTestId('material-form').getByRole('button', { name: 'Add material' }).click();
  let materials = (await api('GET', '/inventory/materials')).items;
  expect(materials).toHaveLength(1);
  const material = materials[0];
  const materialRow = page.getByTestId(`material-${material.ref}`);
  await expect(materialRow).toContainText('Acceptance red');
  await materialRow.getByRole('button', { name: 'Edit' }).click();
  await page.getByLabel('Material name').fill('Acceptance silk red');
  await page.getByRole('button', { name: 'Save material' }).click();
  await expect(materialRow).toContainText('Acceptance silk red');

  await page.getByRole('button', { name: 'Spools', exact: true }).click();
  await page.getByRole('button', { name: 'Add spool', exact: true }).click();
  await page.getByLabel('Spool material').selectOption(material.ref);
  await page.getByLabel('Storage location').fill('Shelf A');
  await page.getByLabel('Initial weight', { exact: true }).fill('750');
  await page.getByTestId('spool-form').getByRole('button', { name: 'Add spool' }).click();
  const spools = (await api('GET', '/inventory/spools')).items;
  expect(spools).toHaveLength(1);
  const spool = spools[0], row = page.getByTestId(`spool-${spool.ref}`);
  await expect(row).toContainText('750 g / 750 g');
  await row.getByRole('button', { name: 'Set weight' }).click();
  await page.getByLabel(`New weight for spool ${spool.ref}`).fill('412');
  await page.getByRole('button', { name: 'Set', exact: true }).click();
  await expect(row).toContainText('412 g / 750 g');
  await row.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Storage location').fill('Drawer 3');
  await page.getByRole('button', { name: 'Save spool' }).click();
  await expect(row).toContainText('Drawer 3');
  await page.reload();
  await expect(row).toContainText('412 g / 750 g');

  await page.goto('/fleet');
  await page.locator('.card').filter({ hasText: printer.name }).first().click({ position: { x: 100, y: 20 } });
  await page.getByRole('button', { name: 'Change', exact: true }).click();
  await page.getByPlaceholder('Search spools…').focus();
  await page.getByText(new RegExp(`#${spool.ref} Fixture vendor Acceptance silk red`)).click();
  await page.getByRole('button', { name: 'Save filaments' }).click();
  await expect.poll(async () => (await api('GET', `/printers/${printer.id}`)).loaded_filaments[0].inventory?.spool_ref).toBe(spool.ref);

  const upload = await request.post('/api/v1/files/upload', { headers: { 'X-Api-Key': key }, multipart: {
    file: { name: 'plate.3mf', mimeType: 'application/octet-stream', buffer: await readFile(fileURLToPath(new URL('../e2e/fixtures/plate.3mf', import.meta.url))) },
  } });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const file = await upload.json();
  const job = await api('POST', '/jobs', { uploaded_file_id: file.id, plate_number: 1,
    printer_configs: [{ printer_id: printer.id, print_profile: 'Fixture process', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000' }] });
  let detail = await api('GET', `/jobs/${job.id}/details`);
  expect(detail.printer_configs[0].filament_match.eligible).toBe(true);
  await page.goto(`/jobs/${job.id}`);
  await expect(page.getByTestId('filament-match-reasons')).toHaveCount(0);
  const slots = (await api('GET', `/printers/${printer.id}`)).loaded_filaments;
  await api('PATCH', `/printers/${printer.id}`, { loaded_filaments: slots.map((s: object) => ({ ...s, inventory: null, type: 'PLA', subtype: 'standard', color: '#FF0000' })) });
  await page.reload();
  await expect(page.getByTestId('filament-match-reasons')).toContainText('Filament doesn\'t match');
  detail = await api('GET', `/jobs/${job.id}/details`);
  expect(detail.printer_configs[0].filament_match.eligible).toBe(false);

  await page.goto('/library');
  await page.getByTestId(`spool-${spool.ref}`).getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByTestId(`spool-${spool.ref}`)).toHaveCount(0);
  await page.getByLabel('Show archived').check();
  await expect(page.getByTestId(`spool-${spool.ref}`)).toContainText('archived');
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  await page.getByTestId(`material-${material.ref}`).getByRole('button', { name: 'Archive' }).click();
  await page.getByLabel('Show archived').uncheck();
  await expect(page.getByTestId(`material-${material.ref}`)).toHaveCount(0);
  await page.getByLabel('Show archived').check();
  await expect(page.getByTestId(`material-${material.ref}`)).toContainText('archived');
  materials = (await api('GET', '/inventory/materials?include_archived=true')).items;
  expect(materials[0].archived).toBe(true);

  await page.goto('/plugins/local_inventory');
  await page.getByRole('switch').first().click();
  await expect(page.getByTestId('plugin-disabled')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Local inventory', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Filament library', exact: true })).toHaveCount(0);
  await page.goto('/library');
  await expect(page.getByTestId('library-unavailable')).toBeVisible();
  await page.goto('/plugins/local_inventory/settings');
  await expect(page.getByTestId('plugin-disabled')).toBeVisible();
});
