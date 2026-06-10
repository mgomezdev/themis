import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('fleet loads with mocked printers', async ({ page }) => {
  await mockApi(page);
  await page.goto('/fleet');
  // Verify the Fleet page loaded with mocked data by checking page text
  const bodyText = await page.locator('body').textContent();
  expect(bodyText).toMatch(/printers online|Workshop|Fleet/);
});
