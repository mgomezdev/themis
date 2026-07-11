import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test.describe('Fleet — loaded filament slots', () => {
  test('U1 shows its loaded slots and an Add-slot affordance', async ({ page }) => {
    await mockApi(page);
    await page.goto('/fleet');

    // Wait for the Fleet page to finish rendering.
    await expect(page.getByText(/\d+\s+printers/).first()).toBeVisible();

    // Expand the U1 printer card. The tile is a <div onClick> where the center may be
    // obscured by the "Ready for new work" button — click the U1 nickname span in the
    // header area instead (top area of the tile, not covered by the overlay button).
    const u1Card = page.locator('.card').filter({ hasText: 'U1' }).first();
    // Wait for the card to be visible before attempting to get its bounding box.
    await expect(u1Card).toBeVisible();
    const box = await u1Card.boundingBox();
    // Click ~20px from the top of the card (the header area, above any overlay buttons).
    await page.mouse.click(box!.x + box!.width / 2, box!.y + 20);

    // The expanded card shows "Loaded filament(s)" with the slot names as visible text.
    await expect(page.getByText('Loaded filament')).toBeVisible();
    await expect(page.getByText('PLA White')).toBeVisible();
    await expect(page.getByText('PETG Black')).toBeVisible();

    // Click "Change" to open the FilamentPicker inline editor.
    await page.getByRole('button', { name: 'Change' }).click();

    // The FilamentPicker renders each slot as <input placeholder="Filament name" value={s.name}>.
    // All four mocked filament-slot names must be present.
    const nameInputs = page.locator('input[placeholder="Filament name"]');
    await expect(nameInputs).toHaveCount(4);
    await expect(nameInputs.nth(0)).toHaveValue('PLA White');
    await expect(nameInputs.nth(1)).toHaveValue('PETG Black');
    await expect(nameInputs.nth(2)).toHaveValue('TPU Green');
    await expect(nameInputs.nth(3)).toHaveValue('PLA Blue');

    // The "Add slot" button must be present in the editor.
    await expect(page.getByRole('button', { name: /add slot/i })).toBeVisible();
  });
});
