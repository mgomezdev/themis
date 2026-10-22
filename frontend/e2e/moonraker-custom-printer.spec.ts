import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

/**
 * BIZ-148 acceptance: a generic/custom Klipper printer is added through the normal wizard; the bed size and toolhead count the user
 * states are what gets stored (sent to the API), while a declared model of the SAME plugin needs neither.
 */

const FIELDS = (toolheads: number) => [
  { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
  { name: 'port', label: 'Moonraker port', field_type: 'number', required: false, default: 7125, placeholder: '', help_text: '' },
  { name: 'api_key', label: 'API key', field_type: 'password', required: false, default: null, placeholder: '', help_text: '' },
  { name: 'toolheads', label: 'Toolheads / extruders', field_type: 'number', required: false, default: toolheads, placeholder: '', help_text: '' },
];
const type = (mfr: string, mfrName: string, model: string, name: string, over: Record<string, unknown> = {}) => ({
  plugin_id: 'moonraker', manufacturer_id: mfr, manufacturer_name: mfrName, model_id: model, display_name: name, bed_mm: [250, 250],
  toolheads: 1, custom: false, plugin_enabled: true, model_enabled: true, model_uuid: `uuid-${model}`, connection_fields: FIELDS(1), ...over,
});

test('a custom Klipper printer is added with the bed and toolheads the user states', async ({ page }) => {
  const mocks = await mockApi(page);
  await page.route('**/api/v1/printers/types', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
    type('voron', 'Voron Design', 'v2_4_300', '2.4 (300 mm)', { bed_mm: [300, 300] }),
    type('generic', 'Generic', 'custom_klipper', 'Custom Klipper printer', { custom: true }),
  ]) }));
  await page.route('**/api/v1/printers/orca-machine-catalog', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.goto('/fleet');

  await page.getByText('Add printer').first().click();
  await page.getByLabel('Manufacturer').selectOption('generic');
  await page.getByRole('button', { name: /^Next/ }).click();                       // → connect
  await page.getByText('IP Address').locator('..').locator('input').fill('192.168.1.60');
  await page.getByText('Toolheads / extruders (optional)').locator('..').locator('input').fill('2');
  await page.getByLabel('Bed width X (mm)').fill('410');
  await page.getByLabel('Bed depth Y (mm)').fill('205');
  await page.getByRole('button', { name: /^Next/ }).click();                       // → profile
  await page.getByRole('button', { name: /^Next/ }).click();                       // → review
  await page.getByRole('button', { name: /Finish/ }).click();

  await expect.poll(() => mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).toMatchObject({
    plugin_id: 'moonraker', manufacturer_id: 'generic', model_id: 'custom_klipper', bed_x_mm: 410, bed_y_mm: 205,
    connection_config: { ip_address: '192.168.1.60', toolheads: '2', port: '7125' },
  });
});

test('a declared Voron model of the same plugin is added without asking for a bed size', async ({ page }) => {
  const mocks = await mockApi(page);
  await page.route('**/api/v1/printers/types', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
    type('voron', 'Voron Design', 'v2_4_300', '2.4 (300 mm)', { bed_mm: [300, 300] }),
  ]) }));
  await page.route('**/api/v1/printers/orca-machine-catalog', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.goto('/fleet');

  await page.getByText('Add printer').first().click();
  await page.getByRole('button', { name: /^Next/ }).click();
  await expect(page.getByLabel('Bed width X (mm)')).toHaveCount(0);
  await page.getByText('IP Address').locator('..').locator('input').fill('192.168.1.61');
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /Finish/ }).click();

  await expect.poll(() => mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).toMatchObject({
    manufacturer_id: 'voron', model_id: 'v2_4_300' });
  expect(mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).not.toHaveProperty('bed_x_mm');
});
