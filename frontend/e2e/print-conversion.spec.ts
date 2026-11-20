import { expect, test } from '@playwright/test';
import { mockApi } from './mock-api';

test('format defaults show only converters that can produce the required printer file', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/v1/capabilities', route => route.fulfill({ json: { capabilities: [{
    id: 'gcode.produce', label: 'Print artifact converter', mode: 'choose_one', status: 'serving',
    description: '', definer: null, requires_by: [], providers: [], selected: null,
  }] } }));

  let rawProvider: string | null = null;
  let archiveProvider: string | null = null;
  const endpoint = '/api/v1/settings/print-conversion';
  await page.route(`**${endpoint}`, route => route.fulfill({ json: {
    providers: [
      { id: 'raw', name: 'Raw converter', inputs: ['stl'], outputs: ['gcode'] },
      { id: 'archive', name: 'Archive converter', inputs: ['stl'], outputs: ['gcode_3mf'] },
      { id: 'model_archive', name: '3MF-only converter', inputs: ['model_3mf'], outputs: ['gcode_3mf'] },
    ],
    routes: [
      { input_kind: 'stl', output_kind: 'gcode', provider_plugin_id: rawProvider },
      { input_kind: 'stl', output_kind: 'gcode_3mf', provider_plugin_id: archiveProvider },
    ],
    brands: [],
  } }));
  await page.route(`**${endpoint}/routes/stl/*`, route => {
    const { provider_plugin_id } = route.request().postDataJSON();
    if (new URL(route.request().url()).pathname.endsWith('/gcode_3mf')) archiveProvider = provider_plugin_id;
    else rawProvider = provider_plugin_id;
    return route.fulfill({ json: { provider_plugin_id } });
  });

  await page.goto('/settings/capabilities');
  const raw = page.getByRole('combobox', { name: '.stl → .gcode', exact: true });
  const archive = page.getByRole('combobox', { name: '.stl → .gcode.3mf', exact: true });
  await expect(raw.locator('option')).toHaveText(['Capability default', 'Raw converter']);
  await expect(archive.locator('option')).toHaveText(['Capability default', 'Archive converter']);
  await raw.selectOption('raw');
  await expect(raw).toHaveValue('raw');
  await archive.selectOption('archive');
  await expect(archive).toHaveValue('archive');
  await page.reload();
  await expect(raw).toHaveValue('raw');
  await expect(archive).toHaveValue('archive');
});

test('queues a print-ready G-code archive for its printer without a converter or print profile', async ({ page }) => {
  const profilesRequested: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.endsWith('/profiles')) profilesRequested.push(request.url());
  });
  const mocks = await mockApi(page, {
    printers: [{
      id: 21, name: 'Archive printer', printer_type: 'bambu', plugin_id: 'bambu',
      model_uuid: 'model-p1s', model_label: 'Bambu Lab P1S', print_artifact_kind: 'gcode_3mf', connected: true, enabled: true,
      queue_on: true, loaded_filaments: [],
    }],
    files: [{
      id: 7, original_filename: 'ready.gcode.3mf', folder: '/', plate_count: 1, kind: 'gcode_3mf',
      sliced_version_count: 0, eligibility: { known: true, model_uuids: ['model-p1s'] },
    }],
    plates: [{ plate_number: 1, estimated_time: 60, filament_g: 2, thumbnail_path: null }],
  });

  await page.goto('/queue/new');
  await page.getByRole('button', { name: /Pick from library/i }).click();
  await page.getByRole('button', { name: /ready\.gcode\.3mf/ }).click();
  await page.getByRole('button', { name: '+ Printer' }).click();
  await page.getByRole('menuitem', { name: /^Archive printer/ }).click();
  await expect(page.getByTestId('print-profile-select')).toHaveCount(0);
  await page.getByRole('button', { name: /Add.*job.*queue/i }).click();
  await expect.poll(() => mocks.captured.find(call => call.method === 'POST' && call.url === '/jobs')?.body)
    .toMatchObject({ uploaded_file_id: 7, plate_number: 1, printer_configs: [{ printer_id: 21 }] });
  expect(profilesRequested).toEqual([]);
});
