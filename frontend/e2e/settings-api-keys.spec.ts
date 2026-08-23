import { test, expect } from '@playwright/test';

test.describe('API Keys management', () => {
  // Test the real bootstrap flow without pre-seeded localStorage.
  // Sets up custom route mocks for the create/reveal/revoke flow.

  test('create, reveal, and revoke API keys through the Settings UI', async ({ page }) => {
    // Mock WebSocket to prevent hanging on ws connections
    await page.addInitScript(() => {
      (window as any).WebSocket = class MockWebSocket {
        onmessage: ((e: MessageEvent) => void) | null = null;
        onopen: (() => void) | null = null;
        onclose: (() => void) | null = null;
        constructor(_url: string) {
          setTimeout(() => this.onopen?.(), 0);
        }
        send() {}
        close() {}
      };
    });

    // Track created keys in memory
    let nextKeyId = 1;
    const keys = new Map<number, any>();

    // Mock all /api/v1/* endpoints
    await page.route('**/api/v1/**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname.replace(/^\/api\/v1/, '');
      const method = req.method();

      const ok = (body = {}) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });

      // Handle bootstrap (POST with name: 'Browser') vs create (POST with arbitrary name)
      if (method === 'POST' && path === '/api-keys') {
        let body: any = {};
        try {
          body = req.postDataJSON() || {};
        } catch (e) {
          // No JSON body (maybe empty POST for bootstrap validation)
        }
        if (body.name === 'Browser') {
          // Bootstrap response
          return ok({
            id: 0,
            name: 'Browser',
            key_prefix: 'thm_e2e_bootstrap',
            scopes: [],
            enabled: true,
            created_at: new Date().toISOString(),
            last_used_at: null,
            revoked_at: null,
            key: 'thm_e2e_bootstrap_key',
          });
        }
        // Create response
        const keyId = nextKeyId++;
        const created = {
          id: keyId,
          name: body.name || 'Unnamed',
          key_prefix: 'thm_e2e_' + keyId,
          scopes: body.scopes || [],
          enabled: true,
          created_at: new Date().toISOString(),
          last_used_at: null,
          revoked_at: null,
          key: 'thm_e2e_' + keyId + '_' + Math.random().toString(36).slice(2),
        };
        keys.set(keyId, created);
        return ok(created);
      }

      // GET /api-keys: return all keys
      if (method === 'GET' && path === '/api-keys') {
        return ok(Array.from(keys.values()).concat(keys.has(0) ? [] : []));
      }

      // GET /api-keys/scopes: return available scopes
      if (method === 'GET' && path === '/api-keys/scopes') {
        return ok(['files:read', 'files:write', 'jobs:read', 'jobs:write', 'printers:read', 'printers:write']);
      }

      // POST /api-keys/{id}/revoke
      if (method === 'POST' && path.match(/^\/api-keys\/\d+\/revoke$/)) {
        const keyId = parseInt(path.match(/\d+/)![0], 10);
        const key = keys.get(keyId);
        if (key) {
          key.enabled = false;
          key.revoked_at = new Date().toISOString();
        }
        return ok({});
      }

      // Minimal responses for app shell endpoints
      if (path === '/printers') return ok([]);
      if (path === '/fleet') return ok([]);
      if (path === '/files') return ok([]);
      if (path === '/jobs') return ok([]);
      if (path === '/orders') return ok([]);
      if (path === '/queue') return ok([]);
      if (path === '/queue/config' || path === '/settings/queue') return ok({ check_interval_minutes: 5 });
      if (path === '/settings/spoolman') return ok({ enabled: false });
      if (path === '/laminus/catalog/status') return ok({ cached: false, laminus: { catalog_building: false } });
      if (path === '/machine-catalog') return ok([]);

      // Default: return empty object for unlisted endpoints
      return ok({});
    });

    // Navigate to the app (no pre-seeded key → bootstrap fires)
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Wait for AuthGate to pass (check for sidebar visibility)
    await expect(page.locator('a[href="/fleet"]')).toBeVisible({ timeout: 5000 });

    // Navigate to API Keys settings
    await page.goto('/settings/api-keys');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h2:has-text("API Keys")')).toBeVisible({ timeout: 5000 });

    // Click "Create key" button
    await page.getByRole('button', { name: /Create key/i }).click();
    await expect(page.locator('h2:has-text("Create API key")')).toBeVisible({ timeout: 5000 });

    // Fill in name
    await page.locator('input[placeholder*="Ordinus"]').fill('E2E Test Key');

    // Click "All scopes" button
    await page.getByRole('button', { name: /All scopes/i }).click();

    // Click "Create key" in modal footer
    await page.locator('div[style*="position: fixed"]').getByRole('button', { name: /Create key/i }).click();

    // Wait for reveal dialog
    await expect(page.locator('h2:has-text("Key created")')).toBeVisible({ timeout: 5000 });

    // Verify key is shown
    const keyValue = await page.locator('input.mono[readonly]').inputValue();
    expect(keyValue).toMatch(/thm_e2e_\d+_/);

    // Close reveal dialog
    await page.getByRole('button', { name: /Done.*saved/i }).click();
    await expect(page.locator('h2:has-text("Create API key")')).not.toBeVisible();

    // Verify key appears in list
    await expect(page.locator('text=E2E Test Key')).toBeVisible({ timeout: 5000 });

    // Verify status is "Active"
    await expect(page.locator('text=Active')).toBeVisible();

    // Set up dialog handler before clicking (dialog is triggered synchronously)
    page.once('dialog', (d) => {
      if (d.type() === 'confirm') {
        d.accept();
      }
    });

    // Click Revoke button
    const revokeBtn = page.getByRole('button', { name: /Revoke/i });
    await revokeBtn.click();

    // Wait for the refetch to complete and the key to show as Revoked
    // The dialog handler should accept the confirm, then the app refetches the keys
    await page.waitForLoadState('networkidle');

    // Verify key now shows as "Revoked"
    await expect(page.locator('text=Revoked')).toBeVisible({ timeout: 5000 });

    // Verify Delete button is now visible (Revoke button is replaced when revoked)
    await expect(page.getByRole('button', { name: /Delete/i })).toBeVisible();
  });
});
