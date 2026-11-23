import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

const phones = [
  { name: 'Android 360', width: 360, height: 800 },
  { name: 'Android 412', width: 412, height: 915 },
];

const routes = [
  '/queue', '/queue/new', '/fleet', '/wall', '/files', '/printer-files', '/projects/new',
  '/orders/new', '/analytics', '/settings/tags', '/settings/print', '/settings/api-keys', '/settings/plugins',
];

async function setup(page: Page) {
  await mockApi(page, { fleet: [{
    id: 1, name: 'Forge Alpha With A Long Name', printer_type: 'bambu', connected: true, enabled: true,
    queue_on: true, awaiting_plate_clear: false, state: 'IDLE', progress: 0, remaining_time: 0,
    temperatures: { nozzle: 25, bed: 25 }, loaded_filaments: [], capabilities: { camera: true }, current_print: null,
  }] });
  await page.route('**/api/v1/printers/files/all', route => route.fulfill({ json: { printers: [{
    printer_id: 1, printer_name: 'Forge Alpha With A Long Name', error: null, can_delete: true, can_download: true,
    files: [{ id: 'long.gcode', name: 'a-very-long-printer-file-name-that-must-wrap-cleanly.gcode', size: 12345,
      modified_at: '2026-11-22T00:00:00Z', is_dir: false, metadata: { estimated_seconds: 3600, filament_grams: 12 }, printable: true }],
  }] } }));
  await page.route(/\/api\/v1\/fleet\/analytics\?/, route => route.fulfill({ json: {
    range: { start: '2026-11-01', end: '2026-11-22', days: 22 },
    totals: { completed: 8, failed: 1, cancelled: 0, success_rate: 88.9, print_seconds: 36000, filament_grams: 1500, filament_cost: 42.5 },
    printers: [{ printer_id: 1, name: 'Forge Alpha With A Long Name', completed: 8, failed: 1, cancelled: 0,
      success_rate: 88.9, print_seconds: 36000, filament_grams: 1500, filament_cost: 42.5, utilization_pct: 18.5 }],
    materials: [{ material: 'PLA', grams: 1500, jobs: 8 }],
  } }));
  await page.route('**/api/v1/api-keys', route => route.fulfill({ json: [{
    id: 1, name: 'Workshop automation with a long descriptive name', key_prefix: 'thm_mobile', scopes: ['fleet:read'],
    enabled: true, created_at: '2026-11-01T00:00:00Z', last_used_at: null,
  }] }));
  await page.route('**/api/v1/api-keys/scopes', route => route.fulfill({ json: ['fleet:read'] }));
}

async function expectNoHorizontalOverflow(page: Page, expectedWidth: number) {
  await expect.poll(() => page.evaluate(() => {
    const content = document.querySelector('.content') as HTMLElement;
    const right = content.getBoundingClientRect().right;
    return {
      viewport: window.innerWidth,
      phoneMedia: window.matchMedia('(max-width: 600px)').matches,
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      content: content.scrollWidth - content.clientWidth,
      offenders: [...content.querySelectorAll<HTMLElement>('*')]
        .filter(el => {
          const box = el.getBoundingClientRect();
          return box.width > 0 && box.right > right + 1;
        })
        .slice(0, 8)
        .map(el => `${el.tagName.toLowerCase()}.${el.className || '-'}:${Math.round(el.getBoundingClientRect().right - right)}px`),
    };
  })).toMatchObject({ viewport: expectedWidth, phoneMedia: true, document: 0, content: 0 });
}

for (const phone of phones) {
  test.describe(phone.name, () => {
    test.use({ viewport: { width: phone.width, height: phone.height }, hasTouch: true });

    for (const route of routes) {
      test(`${route} has no horizontal overflow`, async ({ page }) => {
        await setup(page);
        await page.goto(route);
        await expect(page.locator('.content')).toBeVisible();
        await expectNoHorizontalOverflow(page, phone.width);
      });
    }

    test('mobile-specific presentations and accessible controls are active', async ({ page }) => {
      await setup(page);
      await page.goto('/analytics');
      await expect(page.locator('.analytics-mobile-list')).toBeVisible();
      await expect(page.locator('.analytics-table-wrap')).toBeHidden();

      await page.goto('/wall');
      await expect(page.locator('.wall-tile-name-mobile')).toBeVisible();
      expect(await page.locator('.wall-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(2);

      await page.goto('/settings/print');
      const switches = page.getByRole('switch');
      for (let i = 0; i < await switches.count(); i++) await expect(switches.nth(i)).not.toHaveAccessibleName('');
    });
  });
}
