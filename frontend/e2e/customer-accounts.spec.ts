import { test, expect, type Page } from '@playwright/test';

// UI flows for customer accounts. Backend-free like the other specs: a small stateful fake
// stands in for the API. Authorization itself (who may see what) is enforced — and tested —
// server-side in backend/tests/api/test_customer_flows.py; this fake mirrors that contract so
// the UI's wiring (sign-in → role switch → portal, Settings → Customers, Promote) is exercised.

const STAFF_KEY = 'thm_e2e_staff_key';

interface Customer { id: number; name: string; email: string; password: string; enabled: boolean }
interface FakeProject { id: number; name: string; customer_id: number | null; stage: 'draft' | 'planning' | 'queued'; jobs: any[]; items?: any[]; notes?: string | null }  // items: staff ProjectItem shape (only used on the staff view)

function fakeBackend() {
  const customers: Customer[] = [];
  const projects: FakeProject[] = [];
  const sessions = new Map<string, number>(); // session key → customer id
  const captured: { method: string; path: string; body: any }[] = [];
  return { customers, projects, sessions, captured };
}
type Fake = ReturnType<typeof fakeBackend>;

const publicCustomer = (c: Customer) =>
  ({ id: c.id, name: c.name, email: c.email, enabled: c.enabled, created_at: '2026-09-28T00:00:00Z' });

function portalProject(p: FakeProject) {
  return {
    id: p.id, name: p.name, notes: p.notes ?? null, stage: p.stage, due_date: null,
    created_at: '2026-09-28T00:00:00Z', updated_at: '2026-09-28T00:00:00Z',
    items: p.items ?? [], jobs: p.jobs, jobs_total: p.jobs.length,
    jobs_complete: p.jobs.filter(j => j.status === 'complete').length,
  };
}

function staffProject(p: FakeProject) {
  return {
    ...(({ jobs: _jobs, ...rest }) => rest)(portalProject(p)), customer: '', order_type: 'customer', on_hold: false, result_file_id: null,
    source_app: null, source_user: null, source_layout_id: null, amount_paid: null,
    payment_status: 'unpaid', customer_id: p.customer_id, links: [], parts: [],
    estimate_filament_grams_total: null, estimate_seconds_total: null,
    estimate_filament_grams_remaining: null, estimate_seconds_remaining: null,
    actual_filament_grams: null, actual_seconds: null, filament_cost_total: null,
  };
}

/** staffKey: browser holds a staff API key. localAdmin: keyless client on THEMIS_LOCAL_NETWORKS
 *  (the real deployment's admin path — /auth/me says role "admin"). Neither: remote, signed out. */
async function install(page: Page, fake: Fake, opts: { staffKey?: boolean; localAdmin?: boolean } = {}) {
  await page.addInitScript(([key, seed]) => {
    if (seed) window.localStorage.setItem('themis.apiKey', key as string);
    (window as any).WebSocket = class {
      onmessage = null; onopen: (() => void) | null = null; onclose = null;
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      send() {} close() {}
    };
  }, [STAFF_KEY, !!opts.staffKey]);

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\/v1/, '');
    const method = req.method();
    let body: any = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    if (method !== 'GET') fake.captured.push({ method, path, body });

    const key = req.headers()['x-api-key'] ?? null;
    const customerId = key ? fake.sessions.get(key) ?? null : null;
    const isStaff = key === STAFF_KEY;
    const send = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    let m: RegExpMatchArray | null;

    // Table already has keys → bootstrap refused, like a real, set-up install.
    if (method === 'POST' && path === '/api-keys') return send(400, { detail: 'Bootstrap closed' });

    if (path === '/auth/login' && method === 'POST') {
      const c = fake.customers.find(x => x.email === body.email.toLowerCase() && x.password === body.password && x.enabled);
      if (!c) return send(401, { detail: 'Invalid email or password' });
      const sessionKey = `thm_e2e_session_${c.id}_${fake.sessions.size}`;
      fake.sessions.set(sessionKey, c.id);
      return send(200, { key: sessionKey, customer: { id: c.id, name: c.name, email: c.email } });
    }
    if (path === '/auth/me') {
      if (customerId != null) {
        const c = fake.customers.find(x => x.id === customerId)!;
        return send(200, { local: false, role: 'customer', customer: { id: c.id, name: c.name, email: c.email } });
      }
      if (!key && opts.localAdmin) return send(200, { local: true, role: 'admin', customer: null });
      return send(200, { local: false, role: isStaff ? 'staff' : null, customer: null });
    }

    // A key that is neither staff nor a live session (revoked/expired) → 401, like require_scope.
    if (key && !isStaff && customerId == null) return send(401, { detail: 'Missing or invalid API key' });

    // Customer portal: only a customer session, only its own projects, edits only while draft.
    if (path.startsWith('/customer/')) {
      if (customerId == null) return send(403, { detail: 'Customer account required' });
      if (path === '/customer/projects' && method === 'GET')
        return send(200, fake.projects.filter(p => p.customer_id === customerId).map(portalProject));
      if (path === '/customer/projects' && method === 'POST') {
        const p: FakeProject = { id: 100 + fake.projects.length, name: body.name, notes: body.notes ?? null,
                                 customer_id: customerId, stage: 'draft', jobs: [] };
        fake.projects.push(p);
        return send(201, portalProject(p));
      }
      const own = (m = path.match(/^\/customer\/projects\/(\d+)(\/files)?$/))
        ? fake.projects.find(x => x.id === +m[1] && x.customer_id === customerId) : undefined;
      if (!own) return send(404, { detail: 'Project not found' });
      if (own.stage !== 'draft') return send(409, { detail: 'Only draft projects can be edited' });
      if (!m![2] && method === 'PATCH') {
        Object.assign(own, body);
        return send(200, portalProject(own));
      }
      if (m![2] && method === 'POST') {
        // Real route: `file: UploadFile` — multipart with a field named "file", else 422.
        const ctype = req.headers()['content-type'] ?? '';
        const filename = /name="file"; filename="([^"]+)"/.exec(req.postDataBuffer()?.toString() ?? '')?.[1];
        if (!ctype.startsWith('multipart/form-data') || !filename)
          return send(422, { detail: [{ loc: ['body', 'file'], msg: 'Field required' }] });
        own.items = [...(own.items ?? []), { id: (own.items?.length ?? 0) + 1, filename, quantity: 1 }];
        return send(201, portalProject(own));
      }
      return send(404, { detail: 'Not found' });
    }

    // Everything else is staff-only.
    if (!isStaff && !(!key && opts.localAdmin)) return send(customerId != null ? 403 : 401, { detail: 'Forbidden' });

    if (path === '/customers' && method === 'GET') return send(200, fake.customers.map(publicCustomer));
    if (path === '/customers' && method === 'POST') {
      const c: Customer = { id: fake.customers.length + 1, name: body.name, email: body.email.toLowerCase(),
                            password: body.password, enabled: true };
      fake.customers.push(c);
      return send(201, publicCustomer(c));
    }
    if ((m = path.match(/^\/customers\/(\d+)$/)) && method === 'PATCH') {
      const c = fake.customers.find(x => x.id === +m![1])!;
      Object.assign(c, body);
      return send(200, publicCustomer(c));
    }
    if ((m = path.match(/^\/projects\/(\d+)\/promote$/)) && method === 'POST') {
      const p = fake.projects.find(x => x.id === +m![1])!;
      const order = ['draft', 'planning', 'queued'];
      if (order.indexOf(body.stage) <= order.indexOf(p.stage)) return send(409, { detail: 'Stage can only move forward' });
      p.stage = body.stage;
      return send(200, staffProject(p));
    }
    if ((m = path.match(/^\/projects\/(\d+)\/jobs$/))) return send(200, []);
    if ((m = path.match(/^\/projects\/(\d+)$/))) {
      const p = fake.projects.find(x => x.id === +m![1]);
      return p ? send(200, staffProject(p)) : send(404, { detail: 'Not found' });
    }
    if (path === '/projects') return send(200, fake.projects.map(staffProject));
    if (path === '/settings/spoolman') return send(200, { enabled: false });
    if (path === '/queue' || path === '/jobs' || path === '/fleet' || path === '/printers' || path === '/orders')
      return send(200, []);
    return send(200, {});
  });
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/');
  await page.getByPlaceholder('Email').fill(email);
  await page.getByPlaceholder('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('customer signs in and sees only their own projects and jobs', async ({ page }) => {
  const fake = fakeBackend();
  fake.customers.push(
    { id: 1, name: 'Alice', email: 'alice@example.com', password: 'alice-pw', enabled: true },
    { id: 2, name: 'Bob', email: 'bob@example.com', password: 'bob-pw', enabled: true },
  );
  fake.projects.push(
    { id: 10, name: 'Alice Widgets', customer_id: 1, stage: 'queued',
      jobs: [{ id: 501, status: 'printing', plate_number: 1, created_at: '2026-09-28T00:00:00Z', completed_at: null, estimate_seconds: 600 }] },
    { id: 11, name: 'Alice Draft', customer_id: 1, stage: 'draft', jobs: [] },
    { id: 20, name: 'Bob Brackets', customer_id: 2, stage: 'queued',
      jobs: [{ id: 601, status: 'queued', plate_number: 1, created_at: '2026-09-28T00:00:00Z', completed_at: null, estimate_seconds: 60 }] },
    { id: 30, name: 'Internal Jig', customer_id: null, stage: 'queued', jobs: [] },
  );
  await install(page, fake);

  await signIn(page, 'alice@example.com', 'alice-pw');

  await expect(page.getByRole('heading', { name: 'My projects' })).toBeVisible();
  await expect(page.getByText('Alice Widgets')).toBeVisible();
  await expect(page.getByText('Alice Draft')).toBeVisible();
  await expect(page.getByText('Bob Brackets')).toHaveCount(0);
  await expect(page.getByText('Internal Jig')).toHaveCount(0);

  // Portal, not the staff app.
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await expect(page.getByText('Job queue')).toHaveCount(0);

  // Their project's jobs are visible; Bob's job is not.
  await page.getByText('Alice Widgets').click();
  await expect(page.getByText('#501 (plate 1)')).toBeVisible();
  await expect(page.getByText('#601')).toHaveCount(0);
});

test('wrong password shows an error and stays on the sign-in form', async ({ page }) => {
  const fake = fakeBackend();
  fake.customers.push({ id: 1, name: 'Alice', email: 'alice@example.com', password: 'alice-pw', enabled: true });
  await install(page, fake);

  await signIn(page, 'alice@example.com', 'nope');

  await expect(page.getByText('Invalid email or password')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'My projects' })).toHaveCount(0);
});

test('admin creates a customer account, and that customer can then sign in', async ({ browser }) => {
  const fake = fakeBackend();

  // Admin on the local network: no key, no sign-in.
  const adminPage = await browser.newPage();
  await install(adminPage, fake, { localAdmin: true });
  await adminPage.goto('/settings/customers');
  await adminPage.getByPlaceholder('Name').fill('Carol');
  await adminPage.getByPlaceholder('Email').fill('Carol@Example.com');
  await adminPage.getByPlaceholder('Password').fill('carol-pw');
  await adminPage.getByRole('button', { name: /add customer/i }).click();

  await expect(adminPage.getByRole('cell', { name: 'carol@example.com' })).toBeVisible();
  expect(fake.captured).toContainEqual({
    method: 'POST', path: '/customers',
    body: { name: 'Carol', email: 'Carol@Example.com', password: 'carol-pw' },
  });

  // The new account works: a fresh browser (no staff key) signs in as Carol.
  const customerPage = await browser.newPage();
  await install(customerPage, fake);
  await signIn(customerPage, 'carol@example.com', 'carol-pw');
  await expect(customerPage.getByRole('heading', { name: 'My projects' })).toBeVisible();
  await expect(customerPage.getByText('No projects yet')).toBeVisible();
  await adminPage.close();
  await customerPage.close();
});

test('admin disables a customer and the customer can no longer sign in', async ({ browser }) => {
  const fake = fakeBackend();
  fake.customers.push({ id: 1, name: 'Dave', email: 'dave@example.com', password: 'dave-pw', enabled: true });

  const adminPage = await browser.newPage();
  await install(adminPage, fake, { staffKey: true });
  await adminPage.goto('/settings/customers');
  await adminPage.getByRole('button', { name: 'Disable' }).click();
  await expect(adminPage.getByRole('cell', { name: 'Disabled' })).toBeVisible();
  expect(fake.captured).toContainEqual({ method: 'PATCH', path: '/customers/1', body: { enabled: false } });

  const customerPage = await browser.newPage();
  await install(customerPage, fake);
  await signIn(customerPage, 'dave@example.com', 'dave-pw');
  await expect(customerPage.getByText('Invalid email or password')).toBeVisible();
  await adminPage.close();
  await customerPage.close();
});

test('admin promotes a customer draft to planning, then to queued', async ({ page }) => {
  const fake = fakeBackend();
  fake.customers.push({ id: 1, name: 'Alice', email: 'alice@example.com', password: 'x', enabled: true });
  // Has a file, so Generate is disabled only because of the draft stage.
  fake.projects.push({ id: 11, name: 'Alice Draft', customer_id: 1, stage: 'draft', jobs: [],
                       items: [{ id: 1, project_id: 11, file_id: 1, file_name: 'part.stl', quantity: 1,
                                 quantity_completed: 0, quantity_failed: 0, filament_type: 'PLA',
                                 filament_color: '#ffffff', filament_id: null, sort_order: 0 }] });
  await install(page, fake, { staffKey: true });

  await page.goto('/projects/11');
  await expect(page.getByRole('heading', { name: 'Alice Draft' })).toBeVisible();
  const generate = page.getByRole('button', { name: 'Generate…' });
  await expect(generate).toBeDisabled();
  await expect(generate).toHaveAttribute('title', 'Promote to planning before creating jobs');

  await page.getByRole('button', { name: 'Promote to Planning' }).click();
  await expect(page.getByRole('button', { name: 'Promote to Queued' })).toBeVisible();
  await expect(generate).toBeEnabled();  // planning: staff can now create jobs

  await page.getByRole('button', { name: 'Promote to Queued' }).click();
  await expect(page.getByRole('button', { name: /Promote to/ })).toHaveCount(0);

  expect(fake.captured.filter(c => c.path === '/projects/11/promote').map(c => c.body))
    .toEqual([{ stage: 'planning' }, { stage: 'queued' }]);
});

test('customer creates a draft, edits it, uploads a file, and signs out', async ({ page }) => {
  const fake = fakeBackend();
  fake.customers.push({ id: 1, name: 'Alice', email: 'alice@example.com', password: 'pw', enabled: true });
  await install(page, fake);
  await signIn(page, 'alice@example.com', 'pw');
  await expect(page.getByText('No projects yet')).toBeVisible();

  // New request → draft, selected for editing.
  await page.getByPlaceholder('New project request name').fill('Gear Housing');
  await page.getByRole('button', { name: 'New request' }).click();
  await expect(page.getByRole('heading', { name: 'Gear Housing' })).toBeVisible();
  await expect(page.locator('.pill', { hasText: 'Draft' })).toBeVisible();

  // Edit notes.
  await page.getByPlaceholder('Describe what you need').fill('10 units, black PETG');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => fake.projects.find(p => p.name === 'Gear Housing')?.notes).toBe('10 units, black PETG');

  // Upload a model file.
  await page.locator('input[type=file]').setInputFiles({
    name: 'housing.stl', mimeType: 'application/octet-stream', buffer: Buffer.from('solid x\nendsolid x\n'),
  });
  await expect(page.getByText('housing.stl × 1')).toBeVisible();

  expect(fake.captured.filter(c => c.path !== '/api-keys').map(c => `${c.method} ${c.path}`)).toEqual([
    'POST /auth/login',
    'POST /customer/projects',
    'PATCH /customer/projects/100',
    'POST /customer/projects/100/files',
  ]);

  // Sign out → back to the sign-in form, key gone.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('themis.apiKey'))).toBeNull();
});

test('customer whose session expires mid-use is returned to the sign-in form', async ({ page }) => {
  const fake = fakeBackend();
  fake.customers.push({ id: 1, name: 'Alice', email: 'alice@example.com', password: 'pw', enabled: true });
  await install(page, fake);
  await signIn(page, 'alice@example.com', 'pw');
  await expect(page.getByText('No projects yet')).toBeVisible();  // initial list request has landed

  fake.sessions.clear();  // server-side: session expired / revoked

  await page.getByPlaceholder('New project request name').fill('After expiry');
  await page.getByRole('button', { name: 'New request' }).click();

  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'My projects' })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('themis.apiKey'))).toBeNull();
  expect(fake.projects).toEqual([]);
});
