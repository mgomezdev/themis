import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

const JOB = {
  id: 1,
  uploaded_file_id: 1,
  plate_number: 1,
  order_id: null,
  assigned_printer_id: null,
  assigned_printer: null,
  queue_position: 1,
  status: 'queued',
  name: 'Edit me',
  block_reason: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  file: { id: 1, original_filename: 'multi.3mf' },
  plate: { plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null },
  printer_configs: [
    {
      printer_id: 3,
      printer_name: 'U1',
      printer_type: 'snapmaker_extended',
      print_profile: '0.20mm Standard',
      filament_profile: null,
      filament_id: null,
      filament_type: null,
      filament_color: null,
      tool_index: null,
      filament_map: [
        { model_filament: 1, tool_index: 0 },
        { model_filament: 2, tool_index: 1 },
      ],
      slice_failed: false,
      slice_error: null,
    },
  ],
};

test('Edit Job pre-fills filament_map and round-trips it on save', async ({ page }) => {
  const mocks = await mockApi(page, { jobDetails: JOB });
  await page.goto('/jobs/1/edit');

  // Wait for the job details to load and the mapping rows to render
  await expect(page.getByTestId('map-tool-1')).toBeVisible();
  await expect(page.getByTestId('map-tool-2')).toBeVisible();

  // Verify pre-fill: model filament 1 -> tool 0, model filament 2 -> tool 1
  await expect(page.getByTestId('map-tool-1')).toHaveValue('0');
  await expect(page.getByTestId('map-tool-2')).toHaveValue('1');

  // Change model filament 2 -> tool 2
  await page.getByTestId('map-tool-2').selectOption('2');
  await expect(page.getByTestId('map-tool-2')).toHaveValue('2');

  // Click Save & re-queue
  await page.getByRole('button', { name: /Save & re-queue/i }).click();

  // Wait for the PATCH to be captured (navigate away triggers it)
  await page.waitForURL(/\/jobs\/1$/);

  const saved = mocks.captured.find(c => c.method === 'PATCH' && c.url.includes('/configs'));
  expect(saved).toBeTruthy();

  const fm = saved!.body.printer_configs[0].filament_map;
  // model filament 1 stays at tool 0
  expect(fm).toContainEqual({ model_filament: 1, tool_index: 0 });
  // model filament 2 was changed to tool 2
  expect(fm).toContainEqual({ model_filament: 2, tool_index: 2 });
});
