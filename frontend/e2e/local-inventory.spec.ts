import { test, expect, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

/**
 * Architectural acceptance (BIZ-234): with Spoolman disabled and Local inventory enabled, the library, its settings page and the
 * normal inventory UI all work through the neutral `filament_inventory` contract and the plugin UI contributions — and nothing
 * of a plugin's UI remains once it is disabled. A stateful fake of the plugin + inventory API stands in for the backend.
 */

interface Mat { ref: string; name: string; material: string | null; vendor: string | null; color_hex: string | null; density: null; diameter: null; profile_links: null; archived: boolean }
interface Spool { ref: string; material_ref: string; remaining_g: number | null; initial_g: number | null; location: string | null; label: string; archived: boolean }

const LIB = ['TRACKS_WEIGHT', 'WRITE_WEIGHT', 'PROFILE_LINKS_READ', 'PROFILE_LINKS_WRITE', 'LABEL_SCAN', 'MANAGE_MATERIALS', 'MANAGE_SPOOLS'];
const REMOTE_CAPS = ['TRACKS_WEIGHT', 'WRITE_WEIGHT', 'PROFILE_LINKS_READ', 'PROFILE_LINKS_WRITE', 'LABEL_SCAN', 'REMOTE'];

function fakeBackend() {
  const plugins = {
    spoolman: { id: 'spoolman', name: 'Spoolman', capabilities: REMOTE_CAPS, enabled: true, label: 'Spoolman', tabs: [{ id: 'connection', label: 'Connection', renderer: 'default' }] },
    local_inventory: { id: 'local_inventory', name: 'Local inventory', capabilities: LIB, enabled: false, label: 'Local inventory', tabs: [{ id: 'settings', label: 'Settings', renderer: 'default' }] },
  };
  const state = { slot: 'spoolman' as string | null, materials: [] as Mat[], spools: [] as Spool[], next: 1, defaultG: 1000 };
  const summary = (p: typeof plugins.spoolman) => ({
    id: p.id, name: p.name, version: '1.0.0', description: `${p.name} test plugin`, docs_url: null, source: 'bundled',
    provides: [{ capability: 'inventory.filament', version: 1, features: p.capabilities, selected: state.slot === p.id,
      status: p.enabled && state.slot === p.id ? 'serving' : 'not_selected', waiting_on: [] }], requires: [], optional: [], defines: [],
    enabled: p.enabled, active: p.enabled && state.slot === p.id, error: null,
    ui: { mode: 'page', nav_label: p.label, nav_placement: 'settings', nav_icon: null, tabs: p.tabs },
  });
  const detail = (p: typeof plugins.spoolman) => ({
    ...summary(p), settings: p.id === 'local_inventory' ? { default_initial_g: state.defaultG } : { url: 'http://spoolman.test', sync_interval_minutes: 15, max_disconnect_minutes: null },
    secrets: p.id === 'spoolman' ? { api_key: false } : {}, secret_fields: p.id === 'spoolman' ? ['api_key'] : [], state: {},
    settings_schema: { properties: p.id === 'local_inventory'
      ? { default_initial_g: { type: 'number', title: 'Default spool weight (g)' } }
      : { url: { type: 'string', title: 'Url' }, api_key: { type: 'string', title: 'Api Key' } } },
  });
  const active = () => (state.slot ? plugins[state.slot as keyof typeof plugins] : null);
  const material = (m: Mat) => m;
  const spoolOut = (s: Spool) => ({ ...s, material: state.materials.find(m => m.ref === s.material_ref) ?? null, unsynced: false, url: null });
  const list = (items: unknown[]) => ({ provider: state.slot ?? '', stale: false, as_of: '2026-01-01T00:00:00Z', items });

  async function install(page: Page) {
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname.replace(/^\/api\/v1/, '');
      const method = req.method();
      const body = (() => { try { return req.postDataJSON(); } catch { return null; } })();
      const ok = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      let m: RegExpMatchArray | null;

      if (path === '/plugins') return ok({ plugins: Object.values(plugins).map(summary), selections: { 'inventory.filament': state.slot } });
      if ((m = path.match(/^\/plugins\/(\w+)$/)) && m[1] in plugins) {
        const p = plugins[m[1] as keyof typeof plugins];
        if (method === 'PUT') {
          if (typeof body?.enabled === 'boolean') p.enabled = body.enabled;
          if (body?.settings?.default_initial_g != null) state.defaultG = body.settings.default_initial_g;
        }
        return ok(detail(p));
      }
      if (path === '/capabilities/inventory.filament/provider' && method === 'PUT') { state.slot = body.plugin_id; return ok({ capability: 'inventory.filament', plugin_id: state.slot, explicit: true }); }

      const a = active();
      if (path === '/inventory/sync-status') return ok({ provider: a && a.enabled ? a.id : null, capabilities: a && a.enabled ? a.capabilities : [], enabled: !!a?.enabled });
      if (path === '/inventory/settings') return ok({ provider: state.slot, deduct_on_complete: true, low_stock: { default_g: null, overrides: {} } });
      if (path === '/inventory/pending-writes' || path === '/inventory/tracking') return ok({ provider: null, items: [] });
      if (!a || !a.enabled) {
        if (path.startsWith('/inventory/')) return ok({ error: 'capability_unavailable' }, 409);
        return route.fallback();
      }
      const withArchived = url.searchParams.get('include_archived') === 'true';
      if (path === '/inventory/materials' && method === 'GET') return ok(list(state.materials.filter(x => withArchived || !x.archived)));
      if (path === '/inventory/spools' && method === 'GET') return ok(list(state.spools.filter(x => withArchived || !x.archived).map(spoolOut)));
      if (path === '/inventory/materials' && method === 'POST') {
        const mat: Mat = { ref: String(state.next++), name: body.name, material: body.material ?? null, vendor: body.vendor ?? null, color_hex: body.color_hex ?? null, density: null, diameter: null, profile_links: null, archived: false };
        state.materials.push(mat);
        return ok(material(mat), 201);
      }
      if ((m = path.match(/^\/inventory\/materials\/(\d+)$/)) && method === 'PATCH') {
        const mat = state.materials.find(x => x.ref === m![1])!; Object.assign(mat, body); return ok(mat);
      }
      if ((m = path.match(/^\/inventory\/materials\/(\d+)\/archive$/))) { const mat = state.materials.find(x => x.ref === m![1])!; mat.archived = body.archived; return ok(mat); }
      if (path === '/inventory/spools' && method === 'POST') {
        const mat = state.materials.find(x => x.ref === body.material_ref)!;
        const initial = body.initial_g ?? state.defaultG;
        const s: Spool = { ref: String(state.next++), material_ref: body.material_ref, initial_g: initial, remaining_g: body.remaining_g ?? initial, location: body.location ?? null,
                           label: body.label ?? ([mat.vendor, mat.name].filter(Boolean).join(' ') || 'spool'), archived: false };
        state.spools.push(s);
        return ok(spoolOut(s), 201);
      }
      if ((m = path.match(/^\/inventory\/spools\/(\d+)$/)) && method === 'PATCH') { const s = state.spools.find(x => x.ref === m![1])!; Object.assign(s, body); return ok(spoolOut(s)); }
      if ((m = path.match(/^\/inventory\/spools\/(\d+)\/archive$/))) { const s = state.spools.find(x => x.ref === m![1])!; s.archived = body.archived; return ok(spoolOut(s)); }
      if ((m = path.match(/^\/inventory\/spools\/(\d+)\/remaining$/))) { const s = state.spools.find(x => x.ref === m![1])!; s.remaining_g = body.remaining_g; return ok(spoolOut(s)); }
      return route.fallback();
    });
  }
  return { install, state, plugins };
}

const nav = (page: Page, name: string | RegExp) => page.getByRole('link', { name, exact: typeof name === 'string' });

test.describe('Local inventory as the active provider (architectural acceptance)', () => {
  test('disable Spoolman, enable Local, run the whole library lifecycle, then remove each plugin\'s UI by disabling it', async ({ page }) => {
    const be = fakeBackend();
    await mockApi(page);
    await be.install(page);

    // --- Spoolman is the provider; its settings entry exists
    await page.goto('/settings/inventory');
    await expect(page.getByLabel('Inventory provider')).toHaveValue('spoolman');
    await expect(nav(page, 'Spoolman')).toBeVisible();
    await expect(nav(page, 'Filament library')).toHaveCount(0);                    // Spoolman keeps its library elsewhere

    // --- switch the provider: Local is enabled and selected from the neutral picker
    await page.getByLabel('Inventory provider').selectOption('local_inventory');
    await expect(page.getByLabel('Inventory provider')).toHaveValue('local_inventory');
    await expect(nav(page, 'Local inventory')).toBeVisible();                       // contributed by the plugin, not hard-coded
    await expect(nav(page, 'Filament library')).toBeVisible();

    // --- disable Spoolman: its page contribution disappears
    await nav(page, 'Spoolman').click();
    await expect(page.getByLabel('Url')).toHaveValue('http://spoolman.test');
    await page.getByRole('switch').first().click();
    await expect(page.getByTestId('plugin-disabled')).toBeVisible();                // a disabled plugin has no pages...
    await expect(nav(page, 'Spoolman')).toHaveCount(0);                              // ...and no navigation entry

    // --- Local inventory's own settings page (generated from its settings model)
    await nav(page, 'Local inventory').click();
    await expect(page.getByRole('heading', { name: 'Local inventory' })).toBeVisible();
    await expect(page.getByLabel('Default spool weight (g)')).toHaveValue('1000');
    await page.getByLabel('Default spool weight (g)').fill('750');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByRole('status')).toHaveText('Saved');

    // --- open the library; create a material and a spool
    await nav(page, 'Filament library').click();
    await page.getByRole('button', { name: 'Materials' }).click();
    await page.getByRole('button', { name: 'Add material' }).first().click();
    await page.getByLabel('Material name').fill('PLA Red');
    await page.getByLabel('Vendor').fill('Acme');
    await page.getByLabel('Material type').fill('PLA');
    await page.getByLabel('Colour').fill('#FF0000');
    await page.getByTestId('material-form').getByRole('button', { name: 'Add material' }).click();
    await expect(page.getByTestId('material-1')).toContainText('Acme PLA Red');

    await page.getByRole('button', { name: 'Spools' }).click();
    await page.getByRole('button', { name: 'Add spool' }).first().click();
    await page.getByLabel('Spool material').selectOption('1');
    await page.getByLabel('Storage location').fill('Shelf A');
    await page.getByTestId('spool-form').getByRole('button', { name: 'Add spool' }).click();
    const row = page.getByTestId('spool-2');
    await expect(row).toContainText('Acme PLA Red');
    await expect(row).toContainText('750 g / 750 g');                               // the configured default weight

    // --- edit, change weight and location
    await row.getByRole('button', { name: 'Set weight' }).click();
    await page.getByLabel('New weight for spool 2').fill('412');
    await page.getByRole('button', { name: 'Set', exact: true }).click();
    await expect(row).toContainText('412 g / 750 g');
    await row.getByRole('button', { name: 'Edit' }).click();
    await page.getByLabel('Storage location').fill('Drawer 3');
    await page.getByRole('button', { name: 'Save spool' }).click();
    await expect(row).toContainText('Drawer 3');

    // --- the data shows up in the normal inventory UI: the Fleet slot picker offers the spool
    await page.goto('/fleet');
    await page.locator('.card').filter({ hasText: 'U1' }).first().click({ position: { x: 100, y: 20 } });
    await page.getByRole('button', { name: 'Change' }).click();
    await page.getByPlaceholder('Search spools…').first().focus();
    await expect(page.getByText(/#2 Acme PLA Red PLA/)).toBeVisible();
    await expect(page.getByText('Drawer 3 · 412g left')).toBeVisible();

    // --- archive it: gone from the default view, kept behind "Show archived"
    await nav(page, 'Filament library').click();
    await page.getByTestId('spool-2').getByRole('button', { name: 'Archive' }).click();
    await expect(page.getByTestId('spool-2')).toHaveCount(0);
    await page.getByLabel('Show archived').check();
    await expect(page.getByTestId('spool-2')).toContainText('archived');

    // --- disable Local: its settings page and the library entry are gone, with no stale route content
    await page.goto('/plugins/local_inventory');
    await page.getByRole('switch').first().click();
    await expect(page.getByTestId('plugin-disabled')).toBeVisible();
    await expect(nav(page, 'Local inventory')).toHaveCount(0);
    await expect(nav(page, 'Filament library')).toHaveCount(0);
    await page.goto('/plugins/local_inventory/settings');
    await expect(page.getByTestId('plugin-disabled')).toBeVisible();
    await page.goto('/library');
    await expect(page.getByTestId('library-unavailable')).toBeVisible();
    await page.goto('/plugins/spoolman/connection');
    await expect(page.getByTestId('plugin-disabled')).toBeVisible();               // and Spoolman's page is not there either
    await expect(nav(page, 'Spoolman')).toHaveCount(0);
  });
});
