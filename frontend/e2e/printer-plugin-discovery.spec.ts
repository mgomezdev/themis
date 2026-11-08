import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('Bambu settings add a scanned printer in place with only the quick-add fields', async ({ page }) => {
  const plugin = {
    id: 'bambu', name: 'Bambu Lab', version: '1.0.0', description: 'Printer plug-in', docs_url: null,
    source: 'bundled', provides: [], requires: [], optional: [], defines: [], enabled: true, active: false, error: null,
    ui: { mode: 'page', nav_label: 'Bambu Lab', nav_placement: 'settings', nav_icon: null, tabs: [] },
    settings: {}, settings_schema: {}, secrets: {}, secret_fields: [], state: {}, printer_discovery: true,
  };
  const mocks = await mockApi(page, {
    plugins: { plugins: [plugin], selections: {} },
    respond: (method, path) => path === '/printers/discover' ? {
      ranges: ['192.168.7.x'], scanned: 254, truncated: false, found: [{
        plugin_id: 'bambu', printer_type: 'bambu', display_name: 'Bambu Lab', model: null, name: null,
        ip: '192.168.7.20', serial: null, model_uuid: null, already_added: false, added_printer: null, ip_in_use_by: null,
        connection_config: { ip_address: '192.168.7.20', serial_number: '' },
        note: "Serial number not announced — read it from the printer's screen",
      }],
    } : method === 'POST' && path === '/printers' ? { id: 501, configured: false, config_error: null } : undefined,
  });
  await page.route('**/api/v1/plugins/bambu', route => route.fulfill({ json: plugin }));
  await page.route('**/api/v1/printers/501', route => route.fulfill({ json: { id: 501, configured: true, config_error: null } }));
  await page.route('**/api/v1/printers/types', route => route.fulfill({ json: [{
    plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s',
    display_name: 'P1S', bed_mm: [256, 256], toolheads: 1, custom: false,
    plugin_enabled: true, model_enabled: true, model_uuid: null,
    connection_fields: [
      { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, quick_add: false },
      { name: 'serial_number', label: 'Serial Number', field_type: 'text', required: true, default: null, quick_add: true },
      { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null, quick_add: true },
    ],
  }] }));
  await page.goto('/plugins/bambu');
  await expect(page.getByLabel('Network ranges')).toHaveValue('192.168.x.x');
  await page.getByLabel('Network ranges').fill('192.168.7.x');
  await page.getByRole('button', { name: 'Scan', exact: true }).click();
  await expect(page.getByText(/Serial number not announced/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add 192.168.7.20' })).toBeDisabled();       // the model is half the identity
  await page.getByLabel('Model for 192.168.7.20').fill('Bambu Lab P1S');
  await page.getByLabel('Serial Number for 192.168.7.20').fill('TEST-SERIAL');
  await page.getByLabel('Access Code for 192.168.7.20').fill('TEST-ACCESS-CODE');
  await expect(page.getByTestId('row-status')).toHaveText('Ready to add');
  await page.getByRole('button', { name: 'Add 192.168.7.20' }).click();
  await expect(page.getByTestId('row-status')).toHaveText('Added · configured');
  await expect(page.getByLabel('Network ranges')).toBeVisible();                                 // results stay on the page
  expect(mocks.captured.find(c => c.url === '/printers/discover')?.body).toEqual({
    ranges: ['192.168.7.x'], plugin_id: 'bambu',
  });
  expect(mocks.captured.find(c => c.method === 'POST' && c.url === '/printers')?.body).toEqual({
    name: 'P1S 20', plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s', connection_config: {
      ip_address: '192.168.7.20', serial_number: 'TEST-SERIAL', access_code: 'TEST-ACCESS-CODE',
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
    connection_config: { ip_address: '192.168.7.30', port: 7125 }, note: null, already_added: false, added_printer: null, ip_in_use_by: null,
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
  await page.getByRole('button', { name: 'More settings for 192.168.7.30', exact: true }).click();
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
