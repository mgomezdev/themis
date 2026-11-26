import { test, expect, type Page } from '@playwright/test';

// BIZ-405: the plate map in a real browser, on the dev gallery (plate-map.html renders every corpus fixture; no API).
// jsdom cannot lay out SVG, so this checks what the unit tests stub: real hit-testing through getBoundingClientRect +
// the viewBox letterboxing, and the phone-width layout.

/** Click a bed-mm point on a fixture whose bed is 256×256 with origin front-left (view = [x, 256 - y]). The client point
 *  comes from the browser's own getScreenCTM(), not from a copy of clientToView's letterbox maths. */
async function clickBed(page: Page, fixture: string, [x, y]: [number, number]) {
  const svg = page.locator(`[data-fixture="${fixture}"] svg.plate-map-svg`);
  await svg.scrollIntoViewIfNeeded();
  const p = await svg.evaluate((el, v) => {
    const pt = new DOMPoint(v[0], v[1]).matrixTransform((el as SVGSVGElement).getScreenCTM()!);
    return { x: pt.x, y: pt.y };
  }, [x, 256 - y]);
  await page.mouse.click(p.x, p.y);
}
const lastClick = (page: Page, fixture: string) => page.getByRole('status', { name: `${fixture} last click` });

test.describe('plate map gallery', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/plate-map.html'); });

  test('hit-testing follows the concave shape, not its bounding box', async ({ page }) => {
    await clickBed(page, 'concave-l', [50, 50]);                         // the notch: inside the bbox, outside the L
    await expect(lastClick(page, 'concave-l')).toHaveText('Last click: —');
    await clickBed(page, 'concave-l', [50, 25]);
    await expect(lastClick(page, 'concave-l')).toHaveText('Last click: L');
  });

  test('a rotated rectangle is hit inside, missed at its bbox corner', async ({ page }) => {
    await clickBed(page, 'rotated-rect', [150, 105]);
    await expect(lastClick(page, 'rotated-rect')).toHaveText('Last click: —');
    await clickBed(page, 'rotated-rect', [140, 140]);
    await expect(lastClick(page, 'rotated-rect')).toHaveText('Last click: Rotated');
  });

  test('a 2 mm object is clickable through its minimum hit area; overlap picks the smaller object', async ({ page }) => {
    await clickBed(page, 'tiny-object', [107, 101]);
    await expect(lastClick(page, 'tiny-object')).toHaveText('Last click: Pin');
    await clickBed(page, 'overlap', [100, 100]);
    await expect(lastClick(page, 'overlap')).toHaveText('Last click: Small');
    await expect(page.locator('[data-fixture="overlap"] .plate-map-row[aria-current="true"]')).toHaveAttribute('data-label', 'Small');
  });

  test('a non-selectable object on top swallows the click', async ({ page }) => {
    await clickBed(page, 'mixed-selectable', [80, 190]);
    await expect(lastClick(page, 'mixed-selectable')).toHaveText('Last click: —');
    await clickBed(page, 'mixed-selectable', [50, 240]);
    await expect(lastClick(page, 'mixed-selectable')).toHaveText('Last click: Under');
  });

  test('off-bed objects are inside the drawn picture', async ({ page }) => {
    const section = page.locator('[data-fixture="off-bed"]');
    const svgBox = (await section.locator('svg.plate-map-svg').boundingBox())!;
    for (const label of ['Beyond', 'Half off']) {
      const box = (await section.locator(`polygon.plate-map-object[data-label="${label}"]`).boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(svgBox.x);
      expect(box.x + box.width).toBeLessThanOrEqual(svgBox.x + svgBox.width + 0.5);
    }
  });

  test('phone width: no sideways scroll, list rows are 44px touch targets', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/plate-map.html');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const row = page.locator('[data-fixture="labels"] .plate-map-row').first();
    expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await clickBed(page, 'square-front-left', [128, 25]);
    await expect(lastClick(page, 'square-front-left')).toHaveText('Last click: Front');
  });
});
