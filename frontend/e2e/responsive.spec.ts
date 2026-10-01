import { test, expect, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

// BIZ-156: Fleet, Queue and Job detail must work on a phone and a wall tablet — no sideways scrolling,
// ≥44px touch targets, shop-floor controls within two taps, and a bottom bar instead of the sidebar.

const printer = (o: Record<string, unknown>) => ({
  connected: true, enabled: true, queue_on: true, awaiting_plate_clear: false, no_snapshots_while_idle: false,
  loaded_filaments: [{ slot: 0, name: 'PLA White', type: 'PLA', color: '#ffffff' }], state: 'IDLE', progress: 0,
  remaining_time: 0, layer_num: null, total_layers: null, temperatures: { nozzle: 25, bed: 25 },
  capabilities: { pause: true, stop: true, camera: true }, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0, ...o,
});
const FLEET = [
  printer({ id: 1, name: 'Forge Alpha Long Printer Name', printer_type: 'bambu', state: 'RUNNING', progress: 42,
    remaining_time: 95, layer_num: 120, total_layers: 300, current_print: '7', awaiting_plate_clear: true }),
  printer({ id: 2, name: 'Centauri', printer_type: 'elegoo_centauri' }),
  printer({ id: 3, name: 'Offline one', printer_type: 'bambu', connected: false }),
];
const job = (id: number, status: string, o: Record<string, unknown> = {}) => ({
  id, uploaded_file_id: 1, plate_number: 1, order_id: null, assigned_printer_id: null, queue_position: id, status,
  overrides: null, block_reason: null, actual_filament_grams: null, actual_seconds: null, actual_filament_breakdown: null,
  deduction_skipped: null, estimate_status: 'done', estimate_seconds: 5400, estimate_filament_grams: 21,
  estimate_filament_breakdown: null, estimate_preset_label: null, created_at: '2026-09-01T00:00:00',
  updated_at: '2026-09-01T00:00:00', materials: ['PLA'], low_stock_warning: null, filament_cost: null,
  eligible_printers: [{ id: 1, name: 'Forge Alpha Long Printer Name' }, { id: 2, name: 'Centauri' }],
  file_name: 'a-really-long-model-file-name-for-testing-overflow.3mf', printer_name: null, ...o,
});
const QUEUE = [job(7, 'printing', { assigned_printer_id: 1 }), job(9, 'queued'),
  job(10, 'blocked', { block_reason: 'Filament mismatch on every eligible printer' })];
const DETAILS = {
  ...QUEUE[0], file: { id: 1, original_filename: 'a-really-long-model-file-name-for-testing-overflow.3mf' },
  plate: { estimated_time: 5400, filament_g: 21, thumbnail_path: null },
  printer_configs: [{ printer_id: 1, printer_name: 'Forge Alpha Long Printer Name', printer_type: 'bambu',
    print_profile: '0.20mm Standard @BBL X1C', filament_profile: 'Generic PLA @System', filament_id: null,
    filament_type: 'PLA', filament_color: 'any', tool_index: null, slice_failed: false, slice_error: null, low_stock_warning: null }],
  assigned_printer: { id: 1, name: 'Forge Alpha Long Printer Name', printer_type: 'bambu' },
  filament_grams_live: 21, estimated_seconds_live: 5400,
};

const setup = (page: Page) => mockApi(page, { fleet: FLEET, printers: FLEET, queue: () => QUEUE, jobDetails: DETAILS });

/** Nothing may need a sideways scroll: neither the page nor the scrolling content pane. */
async function expectNoHorizontalScroll(page: Page) {
  const m = await page.evaluate(() => {
    const content = document.querySelector('.content') as HTMLElement;
    return {
      doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      content: content.scrollWidth - content.clientWidth,
    };
  });
  expect(m).toEqual({ doc: 0, content: 0 });
}

/** Visible buttons/links/selects smaller than 44×44 CSS px (checkbox/radio inputs sit inside labels). */
async function smallTargets(page: Page) {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.querySelectorAll('.content button, .content a, .content select, .bottom-nav button, .topbar button')) {
      const b = el.getBoundingClientRect();
      if (b.width === 0 || b.height === 0 || getComputedStyle(el).visibility === 'hidden') continue;
      if (b.width < 43.5 || b.height < 43.5) {
        out.push(`${el.tagName.toLowerCase()} ${Math.round(b.width)}x${Math.round(b.height)} "${(el.textContent || el.getAttribute('title') || '').trim().slice(0, 30)}"`);
      }
    }
    return out;
  });
}

const SIZES = [
  { name: 'phone 375px', viewport: { width: 375, height: 812 } },
  { name: 'tablet 768px', viewport: { width: 768, height: 1024 } },
  { name: 'tablet landscape 1024px', viewport: { width: 1024, height: 768 } },
];

for (const { name, viewport } of SIZES) {
  test.describe(`${name}`, () => {
    test.use({ viewport, hasTouch: true });

    for (const [label, path, ready] of [
      ['Fleet', '/fleet', '3 printers'],
      ['Queue', '/queue', 'Plate 1'],
      ['Job detail', '/jobs/7', 'Remove from queue'],
    ] as const) {
      test(`${label}: no sideways scroll and ≥44px touch targets`, async ({ page }) => {
        await setup(page);
        await page.goto(path);
        await expect(page.locator('.content').getByText(new RegExp(ready)).first()).toBeVisible();
        await expectNoHorizontalScroll(page);
        expect(await smallTargets(page)).toEqual([]);
      });
    }

    test('Fleet with a printer expanded still fits and keeps ≥44px targets', async ({ page }) => {
      await setup(page);
      await page.goto('/fleet');
      await page.getByText('Forge Alpha Long Printer Name').first().click();
      await expect(page.getByRole('button', { name: /Pause/ })).toBeVisible();
      await expectNoHorizontalScroll(page);
      expect(await smallTargets(page)).toEqual([]);
    });

    test('Queue with a job opened still fits', async ({ page }) => {
      await setup(page);
      await page.goto('/queue');
      await page.getByText('Plate 1').first().click();
      await expect(page.getByRole('button', { name: /Remove from queue/ })).toBeVisible();
      await expectNoHorizontalScroll(page);
    });
  });
}

test.describe('phone shop-floor controls', () => {
  test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

  test('"Ready for new work" is one tap from Fleet', async ({ page }) => {
    const mocks = await setup(page);
    await page.goto('/fleet');
    await page.getByRole('button', { name: /Ready for new work/ }).first().tap();
    await expect.poll(() => mocks.captured.some(c => c.method === 'POST' && c.url === '/printers/1/plate-cleared')).toBe(true);
  });

  test('pause and stop are two taps from Fleet (open the printer, then the button)', async ({ page }) => {
    const mocks = await setup(page);
    await page.goto('/fleet');
    await page.getByText('Forge Alpha Long Printer Name').first().tap();            // 1
    await expect(page.getByRole('button', { name: /Stop/ })).toBeVisible();
    await page.getByRole('button', { name: /Pause/ }).tap();                          // 2
    await expect.poll(() => mocks.captured.some(c => c.method === 'POST' && c.url === '/printers/1/pause')).toBe(true);
  });

  test('the camera feed is on the printer card without any tap', async ({ page }) => {
    await setup(page);
    await page.goto('/fleet');
    await expect(page.locator('.video').first()).toBeVisible();
  });

  test('the sidebar is replaced by a bottom bar whose More sheet reaches the other screens', async ({ page }) => {
    await setup(page);
    await page.goto('/fleet');
    await expect(page.locator('.sidebar')).toBeHidden();
    await expect(page.locator('.bottom-nav')).toBeVisible();

    await page.getByRole('button', { name: 'More' }).tap();
    await page.getByRole('menuitem', { name: 'History' }).tap();
    await expect(page).toHaveURL(/\/history$/);
    await expect(page.getByRole('menu')).toBeHidden();            // the sheet closes on navigation
  });

  test('the Rows layout (a seven-column table) is not offered on a phone', async ({ page }) => {
    await setup(page);
    await page.goto('/fleet');
    await expect(page.locator('.content').getByText('3 printers')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Rows' })).toHaveCount(0);
  });
});

test.describe('desktop is unchanged', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('sidebar and the Rows toggle are present, no bottom bar', async ({ page }) => {
    await setup(page);
    await page.goto('/fleet');
    await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('.bottom-nav')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Rows' })).toBeVisible();
  });
});

test('the app is installable: manifest linked and served', async ({ page, request }) => {
  await setup(page);
  await page.goto('/fleet');
  const href = await page.locator('link[rel="manifest"]').getAttribute('href');
  expect(href).toBe('/manifest.webmanifest');
  const manifest = await (await request.get(href!)).json();
  expect(manifest).toMatchObject({ name: 'Themis', display: 'standalone' });
  expect((await request.get(manifest.icons[0].src)).status()).toBe(200);
});
