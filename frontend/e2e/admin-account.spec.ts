import { test, expect, type Page } from '@playwright/test';

// Admin account UI flows against a small stateful fake of the backend contract in
// backend/app/api/routes/{session,admin_account}.py (auth itself is tested server-side in
// backend/tests/api/test_admin_account.py).

interface Fake {
  password: string | null;
  allowLocal: boolean;
  sessions: Set<string>;
  recoveryCode: string | null;
  captured: { method: string; path: string; body: any }[];
}

const newFake = (over: Partial<Fake> = {}): Fake =>
  ({ password: null, allowLocal: true, sessions: new Set(), recoveryCode: null, captured: [], ...over });

async function install(page: Page, fake: Fake, opts: { local: boolean }) {
  await page.addInitScript(() => {
    (window as any).WebSocket = class {
      onmessage = null; onopen: (() => void) | null = null; onclose = null;
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      send() {} close() {}
    };
  });

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\/v1/, '');
    const method = req.method();
    let body: any = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    if (method !== 'GET') fake.captured.push({ method, path, body });
    const send = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });

    const key = req.headers()['x-api-key'] ?? null;
    const signedIn = key != null && fake.sessions.has(key);
    const localAdmin = key == null && opts.local && fake.allowLocal;
    const account = () => ({ username: 'admin', password_set: fake.password != null, allow_local_login: fake.allowLocal, full_access_keys: 0 });

    if (path === '/auth/me') {
      if (signedIn) return send(200, { local: false, role: 'admin', customer: null });
      return send(200, { local: localAdmin, role: localAdmin ? 'admin' : null, customer: null });
    }
    if (path === '/auth/login' && method === 'POST') {
      if (body.email.toLowerCase() !== 'admin' || fake.password == null || body.password !== fake.password)
        return send(401, { detail: 'Invalid email or password' });
      const k = `thm_e2e_admin_${fake.sessions.size}`;
      fake.sessions.add(k);
      return send(200, { key: k, customer: null, admin: true });
    }
    if (path === '/auth/recover' && method === 'POST') {
      fake.recoveryCode ??= 'ABCDE-FGHJK';  // "written to the server log"
      return send(202, { detail: 'If recovery is available, a one-time code is in the server log.' });
    }
    if (path === '/auth/recover/confirm' && method === 'POST') {
      if (!fake.recoveryCode || body.code.toUpperCase() !== fake.recoveryCode)
        return send(400, { detail: 'Invalid or expired recovery code' });
      fake.password = body.password;
      fake.recoveryCode = null;
      fake.sessions.clear();
      return send(200, { detail: 'Admin password set.' });
    }

    if (!signedIn && !localAdmin) return send(401, { detail: 'Missing or invalid API key' });

    if (path === '/admin-account' && method === 'GET') return send(200, account());
    if (path === '/admin-account/password' && method === 'PUT') {
      fake.password = body.password;
      return send(200, account());
    }
    if (path === '/admin-account' && method === 'PATCH') {
      if (body.allow_local_login === false && fake.password == null)
        return send(409, { detail: 'Set an admin password before requiring sign-in on the local network' });
      fake.allowLocal = body.allow_local_login;
      return send(200, account());
    }
    if (path === '/settings/spoolman') return send(200, { enabled: false });
    if (['/queue', '/jobs', '/fleet', '/printers', '/orders', '/projects', '/customers'].includes(path))
      return send(200, []);
    return send(200, {});
  });
}

async function signIn(page: Page, username: string, password: string) {
  await page.getByPlaceholder('Email or username').fill(username);
  await page.getByPlaceholder('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('admin signs in from off the local network', async ({ page }) => {
  const fake = newFake({ password: 's3cret' });
  await install(page, fake, { local: false });
  await page.goto('/');

  await signIn(page, 'admin', 'wrong');
  await expect(page.getByText('Invalid email or password')).toBeVisible();

  await signIn(page, 'admin', 's3cret');
  await expect(page.locator('a[href="/fleet"]').first()).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('themis.apiKey'))).toMatch(/^thm_e2e_admin_/);
});

test('forgotten admin password is reset with a one-time code from the server log', async ({ page }) => {
  const fake = newFake({ password: 'forgotten' });
  await install(page, fake, { local: false });
  await page.goto('/');

  await page.getByRole('button', { name: /forgot admin password/i }).click();
  await expect(page.getByText(/python -m app.admin reset-password/)).toBeVisible();
  await page.getByRole('button', { name: /write a one-time code/i }).click();
  await expect(page.getByText(/written to the Themis server log/i)).toBeVisible();

  await page.getByPlaceholder('Recovery code').fill('abcde-fghjk');
  await page.getByPlaceholder('New admin password').fill('brand-new');
  await page.getByRole('button', { name: /set admin password/i }).click();
  await expect(page.getByText(/Admin password set/i)).toBeVisible();

  await page.getByPlaceholder('Password', { exact: true }).fill('brand-new');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.locator('a[href="/fleet"]').first()).toBeVisible();
});

test('local admin sets a password, then requires sign-in on the local network', async ({ page }) => {
  const fake = newFake();
  await install(page, fake, { local: true });
  await page.goto('/settings/admin-account');

  const checkbox = page.getByRole('checkbox', { name: /local network devices are admin/i });
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toBeDisabled();  // no password yet → can't lock yourself out
  await expect(page.getByText('Set an admin password before turning this off.')).toBeVisible();

  await page.getByPlaceholder('New admin password').fill('short');
  await expect(page.getByText('At least 8 characters')).toBeVisible();
  await page.getByPlaceholder('New admin password').fill('s3cret-pw');
  await page.getByPlaceholder('Confirm password').fill('s3cret-p');
  await expect(page.getByText('Passwords don’t match')).toBeVisible();
  await page.getByPlaceholder('Confirm password').fill('s3cret-pw');
  await page.getByRole('button', { name: 'Set password' }).click();
  await expect(page.getByText(/Admin password saved/)).toBeVisible();

  await expect(checkbox).toBeEnabled();
  // Controlled input: it only flips once the server accepts the change.
  await checkbox.click();
  await expect.poll(() => fake.allowLocal).toBe(false);
  expect(fake.captured).toContainEqual({ method: 'PUT', path: '/admin-account/password', body: { password: 's3cret-pw' } });
  expect(fake.captured).toContainEqual({ method: 'PATCH', path: '/admin-account', body: { allow_local_login: false } });

  // Same LAN device now has to sign in.
  await page.reload();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await signIn(page, 'admin', 's3cret-pw');
  await expect(page.locator('a[href="/fleet"]').first()).toBeVisible();
});

test('admin account page warns about full-access API keys that bypass sign-in', async ({ page }) => {
  const fake = newFake({ password: 's3cret-pw' });
  await install(page, fake, { local: true });
  await page.route('**/api/v1/admin-account', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(
      { username: 'admin', password_set: true, allow_local_login: true, full_access_keys: 2 }) });
  });
  await page.goto('/settings/admin-account');
  await expect(page.getByText(/2 API keys with full access/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Settings → API Keys' })).toHaveAttribute('href', '/settings/api-keys');
});
