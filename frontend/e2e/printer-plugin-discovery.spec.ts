import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('Bambu settings allow model and serial overrides before explicitly adding the printer', async ({ page }) => {
  const plugin = {
    id: 'bambu', name: 'Bambu Lab', version: '1.0.0', description: 'Printer plug-in', docs_url: null,
    source: 'bundled', provides: [], requires: [], optional: [], defines: [], enabled: true, active: false, error: null,
    ui: { mode: 'page', nav_label: 'Bambu Lab', nav_placement: 'settings', nav_icon: null, tabs: [] },
    settings: {}, settings_schema: {}, secrets: {}, secret_fields: [], state: {}, printer_discovery: true,
  };
  const mocks = await mockApi(page, {
    plugins: { plugins: [plugin], selections: {} },
    respond: (_method, path) => path === '/printers/discover' ? {
      ranges: ['192.168.7.x'], scanned: 254, truncated: false, found: [{
        plugin_id: 'bambu', printer_type: 'bambu', display_name: 'Bambu Lab', model: null, name: null,
        ip: '192.168.7.20', serial: null, model_uuid: null, already_added: false,
        connection_config: { ip_address: '192.168.7.20', serial_number: '' },
        note: "Serial number not announced — read it from the printer's screen",
      }],
    } : undefined,
  });
  await page.route('**/api/v1/plugins/bambu', route => route.fulfill({ json: plugin }));
  await page.route('**/api/v1/printers/types', route => route.fulfill({ json: [{
    plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s',
    display_name: 'P1S', bed_mm: [256, 256], toolheads: 1, custom: false,
    plugin_enabled: true, model_enabled: true, model_uuid: null,
    connection_fields: [
      { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null },
      { name: 'serial_number', label: 'Serial Number', field_type: 'text', required: true, default: null },
      { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null },
    ],
  }] }));
  await page.goto('/plugins/bambu');
  await expect(page.getByLabel('Network ranges')).toHaveValue('192.168.x.x');
  await page.getByLabel('Network ranges').fill('192.168.7.x');
  await page.getByRole('button', { name: 'Scan', exact: true }).click();
  await expect(page.getByText(/Serial number not announced/)).toBeVisible();
  await page.getByRole('button', { name: 'Edit details for 192.168.7.20' }).click();
  await page.getByLabel('Model', { exact: true }).fill('P1S');
  await page.getByLabel('Serial number', { exact: true }).fill('TEST-SERIAL-OVERRIDE');
  await page.getByRole('button', { name: 'Apply details' }).click();
  await expect(page.getByText('Serial: TEST-SERIAL-OVERRIDE')).toBeVisible();
  await page.getByRole('button', { name: 'Use 192.168.7.20', exact: true }).click();
  expect(mocks.captured.find(c => c.url === '/printers/discover')?.body).toEqual({
    ranges: ['192.168.7.x'], plugin_id: 'bambu',
  });
  await expect(page.getByText('Connect to P1S', { exact: true })).toBeVisible();
  await expect(page.getByText('IP Address', { exact: true }).locator('..').locator('input')).toHaveValue('192.168.7.20');
  await expect(page.getByText('Serial Number', { exact: true }).locator('..').locator('input')).toHaveValue('TEST-SERIAL-OVERRIDE');
  await expect(page.getByText('Access Code', { exact: true }).locator('..').locator('input')).toHaveValue('');
  expect(mocks.captured.some(c => c.url === '/printers')).toBe(false);
  await page.getByText('Access Code', { exact: true }).locator('..').locator('input').fill('TEST-ACCESS-CODE');
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /Finish/ }).click();
  await expect(page.getByText('Printer added. It is now available in Printers.')).toBeVisible();
  expect(mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).toMatchObject({
    plugin_id: 'bambu', model_id: 'p1s', connection_config: {
      ip_address: '192.168.7.20', serial_number: 'TEST-SERIAL-OVERRIDE', access_code: 'TEST-ACCESS-CODE',
    },
  });
});

test('Snapmaker settings scan and explicitly add a discovered custom-firmware printer', async ({ page }) => {
  const plugin = {
    id: 'snapmaker', name: 'Snapmaker', version: '1.0.0', description: 'Printer plug-in', docs_url: null,
    source: 'bundled', provides: [], requires: [], optional: [], defines: [], enabled: true, active: false, error: null,
    ui: { mode: 'page', nav_label: 'Snapmaker', nav_placement: 'settings', nav_icon: null, tabs: [] },
    settings: {}, settings_schema: {}, secrets: {}, secret_fields: [], state: {}, printer_discovery: true,
  };
  const found = {
    plugin_id: 'snapmaker', printer_type: 'snapmaker_extended', display_name: 'Snapmaker', model: 'Moonraker / Klipper',
    name: 'Virtual printer', ip: '192.168.7.30', serial: null, model_uuid: null,
    connection_config: { ip_address: '192.168.7.30', port: 7125 }, note: null, already_added: false,
  };
  const mocks = await mockApi(page, {
    plugins: { plugins: [plugin], selections: {} },
    respond: (_method, path) => path === '/printers/discover'
      ? { ranges: ['192.168.x.x'], scanned: 65534, truncated: false, found: [found] } : undefined,
  });
  await page.route('**/api/v1/plugins/snapmaker', route => route.fulfill({ json: plugin }));
  await page.route('**/api/v1/printers/types', route => route.fulfill({ json: [{
    plugin_id: 'snapmaker', manufacturer_id: 'snapmaker', manufacturer_name: 'Snapmaker', model_id: 'u1_extended',
    display_name: 'U1 (Extended)', bed_mm: [270, 270], toolheads: 4, custom: false,
    plugin_enabled: true, model_enabled: true, model_uuid: null,
    connection_fields: [
      { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null },
      { name: 'port', label: 'Moonraker port', field_type: 'number', required: false, default: 7125 },
      { name: 'api_key', label: 'API key', field_type: 'password', required: false, default: null },
    ],
  }] }));
  await page.goto('/plugins/snapmaker');
  await expect(page.getByLabel('Network ranges')).toHaveValue('192.168.x.x');
  await page.getByRole('button', { name: 'Scan', exact: true }).click();
  await page.getByRole('button', { name: 'Use 192.168.7.30', exact: true }).click();
  expect(mocks.captured.find(c => c.url === '/printers/discover')?.body).toEqual({
    ranges: ['192.168.x.x'], plugin_id: 'snapmaker',
  });
  await expect(page.getByText(/could not tell which model/)).toBeVisible();
  expect(mocks.captured.some(c => c.url === '/printers')).toBe(false);
  await page.getByRole('button', { name: /^Next/ }).click();
  await expect(page.getByText('IP Address', { exact: true }).locator('..').locator('input')).toHaveValue('192.168.7.30');
  await expect(page.getByText('API key (optional)', { exact: true }).locator('..').locator('input')).toHaveValue('');
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /^Next/ }).click();
  await page.getByRole('button', { name: /Finish/ }).click();
  await expect(page.getByText('Printer added. It is now available in Printers.')).toBeVisible();
  expect(mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).toMatchObject({
    plugin_id: 'snapmaker', manufacturer_id: 'snapmaker', model_id: 'u1_extended',
    connection_config: { ip_address: '192.168.7.30', port: '7125' },
  });
  expect(await page.evaluate(() => localStorage.getItem('themis.discovery.ranges'))).toBeNull();
});
