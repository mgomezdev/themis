import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

// Slicing cache (BIZ-194) happy path: a model with cached gcode → New Job offers it → the job prints that gcode on
// the printer model it was sliced for.

const MODEL = { id: 1, original_filename: 'multi.3mf', folder: '/', plate_count: 2, kind: '3mf', sliced_version_count: 1, sliced_version: null };
const CACHED = {
  id: 7, original_filename: 'multi - PETG - 0.20mm Standard - Mono.gcode', folder: '/', plate_count: 1, kind: 'gcode',
  sliced_version_count: 0,
  sliced_version: { id: 2, source_file_id: 1, source_filename: 'multi.3mf', plate_number: 1, machine_preset: 'Mono',
                    process_preset: '0.20mm Standard', filament_presets: ['Generic PETG @System'], filament_type: 'PETG',
                    filament_color: '#000000' },
};
const VERSION = {
  id: 2, file_id: 7, name: CACHED.original_filename, kind: 'gcode', plate_number: 1, machine_preset: 'Mono',
  process_preset: '0.20mm Standard', filament_presets: ['Generic PETG @System'], filament_type: 'PETG',
  filament_color: '#000000', bed_type: null, overrides: {}, estimated_seconds: 3600, filament_grams: 12,
  created_at: '2026-10-01T00:00:00Z', source_changed: false, stale: false, stale_reasons: [], printable_now: true,
};

test('New Job offers cached gcode and queues it for the model it was sliced for', async ({ page }) => {
  const mocks = await mockApi(page, {
    files: [MODEL, CACHED], slicedVersions: { 1: [VERSION] },
    platesByFile: { 7: [{ plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null }] },
  });
  await page.goto('/queue/new');
  await page.waitForLoadState('networkidle');

  await page.getByRole('button', { name: /Pick from library/i }).click();
  await page.getByRole('button', { name: /^multi\.3mf/i }).click();

  const prompt = page.getByRole('region', { name: 'Cached sliced versions' });
  await expect(prompt).toBeVisible();
  await expect(prompt.getByText(CACHED.original_filename)).toBeVisible();
  await prompt.getByRole('button', { name: 'Use this version' }).click();

  await expect(page.getByTestId('using-cached')).toBeVisible();
  await page.getByRole('button', { name: /add.*job/i }).click();
  await expect(page.getByText(/added to queue/)).toBeVisible();

  const posts = mocks.captured.filter(c => c.method === 'POST' && c.url === '/jobs');
  expect(posts).toHaveLength(1);   // the cached gcode has one plate
  expect(posts[0].body).toMatchObject({
    uploaded_file_id: 7, printer_configs: [], overrides: null,
    model_targets: [{ machine_profile: 'Mono', print_profile: '', filament_type: 'PETG', filament_color: '#000000' }],
  });
});
