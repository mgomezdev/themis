import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

// One operator journey through the real UI with a stateful mocked API, asserting the request bodies the
// browser actually sends (the other specs mostly check what renders): create a job -> see it in the queue ->
// remove it -> mark the printer ready for new work.

const est = {
  actual_filament_grams: null, actual_seconds: null, actual_filament_breakdown: null, deduction_skipped: null,
  estimate_status: null, estimate_seconds: null, estimate_filament_grams: null, estimate_filament_breakdown: null,
  estimate_preset_label: null, materials: [], eligible_printers: [], model_targets: [], low_stock_warning: null, filament_cost: null,
};
const NEW_JOB = {
  id: 501, uploaded_file_id: 1, plate_number: 1, order_id: null, assigned_printer_id: null, queue_position: 1,
  status: 'queued', overrides: null, block_reason: null, created_at: '2026-09-29T00:00:00Z', updated_at: '2026-09-29T00:00:00Z', ...est,
};
const MONO_FLEET = {
  id: 1, name: 'Mono', printer_type: 'elegoo_centauri', enabled: true, queue_on: true, connected: true, state: 'IDLE',
  awaiting_plate_clear: false, no_snapshots_while_idle: false, loaded_filaments: [], progress: 0, remaining_time: 0,
  layer_num: null, total_layers: null, temperatures: { nozzle: 25, bed: 25 }, capabilities: {}, current_print: null,
  fan_model: 0, fan_aux: 0, fan_box: 0,
};

test('operator golden path: create job, cancel it, clear the plate', async ({ page }) => {
  let queue: any[] = [];
  let awaitingClear = false;
  const mocks = await mockApi(page, {
    plates: [{ plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null }],
    queue: () => queue,
    fleet: () => [{ ...MONO_FLEET, awaiting_plate_clear: awaitingClear }],
    respond: (method, path) => {
      if (method === 'POST' && path === '/jobs') { queue = [NEW_JOB]; return NEW_JOB; }
      if (method === 'POST' && path === '/jobs/501/cancel') { queue = []; return { ...NEW_JOB, status: 'cancelled' }; }
      if (method === 'POST' && path === '/printers/1/plate-cleared') { awaitingClear = false; return { ok: true }; }
      return undefined;
    },
  });
  const posts = (path: string) => mocks.captured.filter(c => c.method === 'POST' && c.url === path);

  // 1. New job from the library: file -> Mono -> print profile -> Add job.
  await page.goto('/queue/new');
  await page.getByRole('button', { name: /Pick from library/i }).click();
  await page.getByRole('button', { name: /multi\.3mf/i }).click();
  await page.waitForLoadState('networkidle');
  await page.getByRole('button', { name: /Mono/i }).first().click();
  await expect(page.getByTestId('print-profile-select')).toBeVisible();
  await page.getByTestId('print-profile-select').selectOption('0.20mm Standard');
  const add = page.getByRole('button', { name: /Add .* jobs? to queue/i });
  await expect(add).toBeEnabled();
  await add.click();

  await expect(page.getByText(/1 job added to queue/)).toBeVisible();
  expect(posts('/jobs')).toHaveLength(1);
  const created = posts('/jobs')[0].body;
  expect(created).toMatchObject({ uploaded_file_id: 1, plate_number: 1, order_id: null, overrides: null });
  expect(created.printer_configs).toHaveLength(1);
  expect(created.printer_configs[0]).toMatchObject({
    printer_id: 1, print_profile: '0.20mm Standard', filament_type: 'any', filament_color: 'any', filament_id: null,
  });

  // 2. The job is in the queue; remove it.
  await page.getByRole('button', { name: 'view queue' }).click();
  await expect(page).toHaveURL(/\/queue$/);
  await page.getByText('Plate 1').first().click();
  await expect(page.getByRole('button', { name: /remove from queue/i })).toBeVisible();
  await page.getByRole('button', { name: /remove from queue/i }).click();

  await expect(page.getByText('Nothing here')).toBeVisible();
  expect(posts('/jobs/501/cancel')).toHaveLength(1);
  expect(posts('/jobs/501/cancel')[0].body).toBeNull();

  // 3. The printer finished something and is waiting for the plate to be cleared.
  awaitingClear = true;
  await page.getByRole('link', { name: 'Fleet' }).first().click();
  await expect(page).toHaveURL(/\/fleet$/);
  const ready = page.getByRole('button', { name: /Ready for new work/ });
  await expect(ready).toBeVisible();
  await ready.click();

  await expect(ready).toHaveCount(0);
  expect(posts('/printers/1/plate-cleared')).toHaveLength(1);
  expect(posts('/printers/1/plate-cleared')[0].body).toBeNull();

  // Nothing else was written along the way.
  expect(mocks.captured.map(c => `${c.method} ${c.url}`)).toEqual([
    'POST /jobs', 'POST /jobs/501/cancel', 'POST /printers/1/plate-cleared',
  ]);
});
