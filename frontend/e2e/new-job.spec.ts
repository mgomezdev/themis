import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Drive the "New Job" flow up to (and including) printer selection for the
 * given printer name. Returns once the PerPrinterConfig panel is visible
 * (print-profile-select is present).
 *
 * Flow:
 *   1. Click "Pick from library" tab
 *   2. Click the file button (multi.3mf)
 *   3. Wait for plates to load (print-profile-select not yet visible, but
 *      PrinterPicker cards appear)
 *   4. Click the printer card for `printerName`
 *   5. Wait for print-profile-select to be visible
 */
async function driveToPerPrinter(page: import('@playwright/test').Page, printerName: string) {
  // Step 1: switch to library source
  await page.getByRole('button', { name: /Pick from library/i }).click();

  // Step 2: select the file
  await page.getByRole('button', { name: /multi\.3mf/i }).click();

  // Step 3: wait for the printer picker to appear (plates loaded)
  await expect(page.getByRole('button', { name: new RegExp(printerName, 'i') })).toBeVisible({ timeout: 5000 });

  // Step 4: click the printer card
  await page.getByRole('button', { name: new RegExp(printerName, 'i') }).first().click();

  // Step 5: wait for PerPrinterConfig to render
  await expect(page.getByTestId('print-profile-select')).toBeVisible({ timeout: 5000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('New Job — filament/tool', () => {

  test('multi-material file: mapping rows + createJob sends filament_map', async ({ page }) => {
    const mocks = await mockApi(page);
    await page.goto('/queue/new');

    // Navigate to library and select the multi-material file
    await page.getByRole('button', { name: /Pick from library/i }).click();
    await page.getByRole('button', { name: /multi\.3mf/i }).click();

    // Wait for printer picker to appear (plates + model-filaments loaded)
    await expect(page.getByRole('button', { name: /U1/i })).toBeVisible({ timeout: 5000 });

    // Select the U1 printer (multi-tool, 4 slots, 2 model filaments → mapping rows)
    await page.getByRole('button', { name: /U1/i }).first().click();

    // PerPrinterConfig should render with mapping rows (f.index is 1-based)
    await expect(page.getByTestId('map-tool-1')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('map-tool-2')).toBeVisible();

    // Select a print profile
    await page.getByTestId('print-profile-select').selectOption({ index: 1 });

    // Change mapping for model filament 2: pick tool index 1 (instead of default 1)
    // Default for filament index 2 is min(2-1, 3) = 1. Change it to 2.
    await page.getByTestId('map-tool-2').selectOption({ index: 2 });

    // Job name is auto-filled to "Plate 1" — verify it's present and non-empty
    const jobNameInput = page.locator('.input[placeholder="e.g. PA-CF arm brackets"]');
    await expect(jobNameInput).toHaveValue(/Plate/i);

    // For multi-plate file the "Add … jobs to queue" button covers all selected plates.
    // With 2 plates both auto-selected, we need both plates to be complete.
    // Switch to plate 2 tab and configure it too.
    const plate2Tab = page.getByRole('button', { name: /Plate 2/i });
    if (await plate2Tab.isVisible()) {
      await plate2Tab.click();

      // Select U1 for plate 2 as well
      await page.getByRole('button', { name: /U1/i }).first().click();
      await expect(page.getByTestId('print-profile-select')).toBeVisible({ timeout: 5000 });
      await page.getByTestId('print-profile-select').selectOption({ index: 1 });

      // Switch back to plate 1
      await page.getByRole('button', { name: /Plate 1/i }).click();
      await expect(page.getByTestId('print-profile-select')).toBeVisible({ timeout: 3000 });
    }

    // Click Create — button text matches "Add … jobs to queue" (disabled until isComplete)
    const createBtn = page.getByRole('button', { name: /Add .* jobs? to queue/i });
    await expect(createBtn).toBeEnabled({ timeout: 3000 });
    await createBtn.click();

    // Wait for at least one POST to be captured
    await page.waitForFunction(
      () => (window as any).__mocks_captured_len > 0,
      {},
      { timeout: 5000 },
    ).catch(() => {}); // may not be wired; fall back to small wait

    // Give the async chain time to dispatch requests
    await page.waitForTimeout(500);

    // Assert createJob payload
    const created = mocks.captured.find(c => c.method === 'POST' && c.url === '/jobs');
    expect(created).toBeTruthy();
    const cfg = created!.body.printer_configs.find((c: any) => c.printer_id === 3);
    expect(cfg).toBeTruthy();
    expect(Array.isArray(cfg.filament_map)).toBe(true);
    expect(cfg.filament_map.length).toBe(2);
  });

  test('single-tool printer defaults to defer', async ({ page }) => {
    const mocks = await mockApi(page);
    await page.goto('/queue/new');

    await driveToPerPrinter(page, 'Mono');

    // MONO has 0 loaded slots → single-tool path → filament-mode select renders
    await expect(page.getByTestId('filament-mode')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('filament-mode')).toHaveValue('defer');

    // Switching to "require" should reveal filament-type-input
    await page.getByTestId('filament-mode').selectOption('require');
    await expect(page.getByTestId('filament-type-input')).toBeVisible({ timeout: 3000 });

    // filament-mode is accessible via mocks too (no-op but ensures mocks is used)
    expect(mocks.captured.length).toBe(0); // no mutations yet
  });

});
