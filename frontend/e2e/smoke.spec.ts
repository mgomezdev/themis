import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('fleet loads with mocked printers', async ({ page }) => {
  await mockApi(page);
  await page.goto('/fleet');
  // Verify the Fleet page loaded with mocked data. Retrying assertion: the shell renders only
  // after AuthGate + the /auth/me role check resolve, so a one-shot read of body text races it.
  await expect(page.locator('body')).toContainText(/printers online|Workshop|Fleet/);
});
