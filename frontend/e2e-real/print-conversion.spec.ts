import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const API_KEY = 'isolated-browser-test-key';
const HEADERS = { 'X-Api-Key': API_KEY };
const CONVERSION = '/settings/print-conversion';

function apiClient(request: APIRequestContext) {
  return async (method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
    expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  };
}

test('persists converter defaults and a nozzle override, then prints G-code with the converter disabled', async ({ page, request }) => {
  await page.addInitScript(key => localStorage.setItem('themis.apiKey', key), API_KEY);
  const api = apiClient(request);

  await api('PUT', '/plugins/mock', { enabled: true });
  await api('PUT', '/plugins/laminus', { enabled: true, settings: { url: 'http://127.0.0.1:9' } });
  const printer = await api('POST', '/printers', { name: 'Conversion high-flow test printer', printer_type: 'mock', connection_config: {} });
  const stock = await api('POST', '/printers', { name: 'Conversion stock test printer', printer_type: 'mock', connection_config: {} });
  await api('PATCH', `/printers/${stock.id}`, { queue_on: false });
  const registry = await api('GET', '/printer-models');
  const model = registry.find((entry: { id: string }) => entry.id === printer.model_uuid);
  expect(model).toBeTruthy();
  const modelPath = `/printer-models/${model.id}`;
  for (const [flow, name] of [['standard', 'Fixture standard'], ['high_flow', 'Fixture high-flow']]) {
    await api('POST', `${modelPath}/slicer-profiles`, {
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow, machine_profile: name,
    });
  }

  await page.goto('/settings/capabilities');
  const raw = page.getByRole('combobox', { name: '.3mf → .gcode', exact: true });
  const archive = page.getByRole('combobox', { name: '.3mf → .gcode.3mf', exact: true });
  await raw.selectOption('laminus');
  await expect(raw).toHaveValue('laminus');
  await expect(archive).toHaveValue('');
  await archive.selectOption('laminus');
  await expect(archive).toHaveValue('laminus');

  await page.getByText('Brand preferences', { exact: true }).click();
  const brand = page.getByRole('combobox', { name: `Preferred converter for ${model.manufacturer_name}`, exact: true });
  await brand.selectOption('laminus');
  await expect(brand).toHaveValue('laminus');
  await page.reload();
  await expect(raw).toHaveValue('laminus');
  await expect(archive).toHaveValue('laminus');
  await page.getByText('Brand preferences', { exact: true }).click();
  await expect(brand).toHaveValue('laminus');
  const stored = await api('GET', CONVERSION);
  expect(stored.routes.filter((route: { provider_plugin_id: string | null }) => route.provider_plugin_id === 'laminus'))
    .toEqual(expect.arrayContaining([
      { input_kind: 'model_3mf', output_kind: 'gcode', provider_plugin_id: 'laminus' },
      { input_kind: 'model_3mf', output_kind: 'gcode_3mf', provider_plugin_id: 'laminus' },
    ]));

  await page.goto('/settings/printer-models');
  const modelRow = page.locator('fieldset > .col').filter({ has: page.getByLabel(`${model.manufacturer_name} ${model.display_name}`, { exact: true }) });
  await modelRow.getByRole('button', { name: 'Slicer profile', exact: true }).click();
  const modelDefault = page.getByRole('combobox', { name: 'Default slicer for this model' });
  await modelDefault.selectOption('laminus');
  await expect(modelDefault).toHaveValue('laminus');

  await page.goto('/fleet');
  await page.getByText(printer.name, { exact: true }).click();
  await page.getByTitle('Edit printer').click();
  await expect(page.getByText('Files sent to this printer use .gcode.')).toBeVisible();
  await page.getByLabel('Nozzle fitted').selectOption('0.4|high_flow');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect.poll(async () => (await api('GET', `/printers/${printer.id}`)).machine_profile).toBe('Fixture high-flow');
  const stockSettings = await api('GET', `/printers/${stock.id}`);
  expect(stockSettings).toMatchObject({ model_slicer_plugin_id: 'laminus', machine_profile: 'Fixture standard', nozzle_variant: null });

  await api('PUT', '/plugins/laminus', { enabled: false });
  const filename = 'print-ready-conversion-fixture.gcode';
  const upload = await request.post('/api/v1/files/upload', { headers: HEADERS, multipart: {
    file: { name: filename, mimeType: 'application/octet-stream', buffer: Buffer.from('; fixture\nG28\n') },
  } });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const file = await upload.json();
  await api('PUT', `/files/${file.id}/eligibility`, { model_uuids: [model.id] });

  await page.goto('/queue/new');
  await page.getByRole('button', { name: /Pick from library/i }).click();
  await page.getByRole('button', { name: new RegExp(filename.replaceAll('.', '\\.')) }).click();
  await page.getByRole('button', { name: '+ Printer' }).click();
  await page.getByRole('menuitem', { name: new RegExp(`^${printer.name}`) }).click();
  const add = page.getByRole('button', { name: /Add.*job.*queue/i });
  await expect(add).toBeEnabled();
  await expect(page.getByTestId('print-profile-select')).toHaveCount(0);
  const submitted = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/jobs');
  await add.click();
  const jobResponse = await submitted;
  expect(jobResponse.ok(), await jobResponse.text()).toBeTruthy();
  const job = await jobResponse.json();
  await expect.poll(async () => {
    const detail = await api('GET', `/jobs/${job.id}/details`);
    return { status: detail.status, assigned_printer_id: detail.assigned_printer_id };
  }, { timeout: 15000 }).toEqual({ status: 'printing', assigned_printer_id: printer.id });
});

test('shows why a model job stays blocked when no converter is enabled', async ({ page, request }) => {
  const api = apiClient(request);
  await page.addInitScript(key => localStorage.setItem('themis.apiKey', key), API_KEY);
  await api('PUT', '/plugins/mock', { enabled: true });
  await api('PUT', '/plugins/laminus', { enabled: false });
  const printer = await api('POST', '/printers', {
    name: 'Printer without a converter', printer_type: 'mock', connection_config: {},
    machine_profile_override: 'Fixture standard',
  });
  await api('PATCH', `/printer-models/${printer.model_uuid}`, { default_slicer_plugin_id: null });
  await api('PUT', `${CONVERSION}/brands/${printer.manufacturer_id}`, { provider_plugin_id: null });
  await api('PUT', `${CONVERSION}/routes/model_3mf/gcode`, { provider_plugin_id: null });
  expect((await api('GET', CONVERSION)).providers).toEqual([]);

  const upload = await request.post('/api/v1/files/upload', { headers: HEADERS, multipart: {
    file: {
      name: 'no-converter-model.3mf', mimeType: 'application/octet-stream',
      buffer: await readFile(new URL('../e2e/fixtures/plate.3mf', import.meta.url)),
    },
  } });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const file = await upload.json();
  const job = await api('POST', '/jobs', {
    uploaded_file_id: file.id, plate_number: 1,
    printer_configs: [{ printer_id: printer.id, print_profile: 'Fixture process' }],
  });

  await expect.poll(async () => (await api('GET', `/jobs/${job.id}/details`)).status).toBe('blocked');
  await page.goto(`/jobs/${job.id}`);
  await expect(page.getByText('Blocked: No slicing provider is enabled — slicing paused', { exact: true })).toBeVisible();
  const detail = await api('GET', `/jobs/${job.id}/details`);
  expect(detail).toMatchObject({ status: 'blocked', assigned_printer_id: null });
});
