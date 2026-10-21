/**
 * Filament matching, end to end (BIZ-312): a job's ask is a tuple (manufacturer, type, subtype, color); a printer slot has its own
 * tuple; a job nothing matches stays queued and its detail says why; the right filament releases it.
 *
 * The same journeys run against EITHER backing, chosen by `E2E_LIVE`:
 *
 *   mocked (default)   `mockApi` + a small stateful stand-in for the few endpoints used. The UI is exercised for real; the
 *                      server's matching verdicts are canned for the one scenario (so these checks prove what the UI
 *                      sends and shows, not that the server matches correctly).
 *   live               E2E_LIVE=1 — a real backend. Printers, jobs and slots are created through the REST API (a "mock"
 *                      printer plugin printer: no hardware), the queue engine really runs, and every verdict below comes
 *                      from the server. Start one with:
 *
 *                        cd backend && THEMIS_DATA_DIR=$(mktemp -d) THEMIS_BOOTSTRAP_KEY=e2e-live-key \
 *                          uvicorn app.main:app --port 8001
 *                        cd frontend && E2E_LIVE=1 npx playwright test e2e/filament-matching.spec.ts
 *
 *                      (Vite proxies /api to :8001. Override with E2E_API_URL / E2E_API_KEY.) The spec enables the bundled
 *                      `mock` printer plugin itself. The real-backend epic acceptance (BIZ-307) reuses `liveWorld`.
 *
 * Both worlds implement `World`, so a journey is written once.
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

const LIVE = process.env.E2E_LIVE === '1';
const API = process.env.E2E_API_URL ?? 'http://localhost:8001';
const KEY = process.env.E2E_API_KEY ?? 'e2e-live-key';

interface Slot { slot: number; type: string; subtype: string | null; manufacturer: string | null; color: string; filament_profile?: string | null }
interface Ask { filament_manufacturer?: string | null; filament_type?: string | null; filament_subtype?: string | null; filament_color?: string | null }

const RED_PLA: Slot = { slot: 0, type: 'PLA', subtype: 'standard', manufacturer: 'Acme', color: '#FF0000' };
const GREEN_PETG: Slot = { slot: 1, type: 'PETG', subtype: 'standard', manufacturer: null, color: '#00FF00' };
const SILK_PLA: Slot = { slot: 2, type: 'PLA', subtype: 'silk', manufacturer: 'Acme', color: '#FF0000' };
const SILK_ASK: Ask = { filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000' };

interface World {
  /** Make the page talk to this world (mock routes, or the API key for a real backend). Call before the first goto. */
  attach(page: Page): Promise<void>;
  printer(slots: Slot[]): Promise<number>;
  job(printerId: number, ask: Ask): Promise<number>;
  /** The operator changes what is loaded. */
  load(printerId: number, slots: Slot[]): Promise<void>;
  /** The job as the server holds it (live) or as the stand-in last served it (mock). */
  jobNow(id: number): Promise<{ status: string; eligible: boolean; ask: Ask & { filament_type: string | null }; saved: Ask | null }>;
}

// --- the live world: a real backend over REST ---------------------------------------------------------------------------------------

export function liveWorld(request: APIRequestContext): World {
  const headers = { 'X-API-Key': KEY };
  const call = async (method: 'get' | 'post' | 'put' | 'patch', path: string, data?: unknown) => {
    const res = await request[method](`${API}/api/v1${path}`, { headers, data });
    expect(res.ok(), `${method.toUpperCase()} ${path}: ${res.status()} ${await res.text()}`).toBeTruthy();
    return res.json();
  };
  let seq = 0;
  let fileId: number | null = null;
  return {
    async attach(page) {
      await page.addInitScript(k => window.localStorage.setItem('themis.apiKey', k), KEY);
      await call('put', '/plugins/mock', { enabled: true });            // idempotent: the bundled hardware-free printer plugin
    },
    async printer(slots) {
      const p = await call('post', '/printers', { name: `E2E ${Date.now()}-${seq++}`, printer_type: 'mock', connection_config: {}, loaded_filaments: slots });
      return p.id;
    },
    async job(printerId, ask) {
      if (fileId === null) {
        // the smallest 3MF the upload route accepts (one plate with a 60 s / 5 g estimate)
        const res = await request.post(`${API}/api/v1/files/upload`, { headers, multipart: { file: { name: 'e2e-filament.3mf', mimeType: 'application/octet-stream', buffer: minimal3mf() } } });
        expect(res.ok(), await res.text()).toBeTruthy();
        fileId = (await res.json()).id;
      }
      const job = await call('post', '/jobs', { uploaded_file_id: fileId, plate_number: 1, printer_configs: [{ printer_id: printerId, print_profile: '0.20mm', ...ask }] });
      return job.id;
    },
    async load(printerId, slots) {
      await call('patch', `/printers/${printerId}`, { loaded_filaments: slots });
      await call('post', `/printers/${printerId}/plate-cleared`);        // wake the queue so the next cycle looks at the new filament
    },
    async jobNow(id) {
      const d = await call('get', `/jobs/${id}/details`);
      const cfg = d.printer_configs[0];
      return { status: d.status, eligible: cfg.filament_match.eligible, ask: cfg, saved: cfg };
    },
  };
}

/** A zipped 3MF with one plate (stored entries, no compression): enough for the upload route. */
function minimal3mf(): Buffer {
  const entries: [string, Buffer][] = [
    ['Metadata/slice_info.config', Buffer.from('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n' +
      '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n    <header_item key="X-BBL-Client-Version" value=""/>\n  </header>\n' +
      '  <plate>\n    <metadata key="index" value="1"/>\n    <metadata key="prediction" value="60"/>\n    <metadata key="weight" value="5.0"/>\n' +
      '    <filament id="1" tray_info_idx="GFL99" type="PLA" color="#FFFFFF" used_m="1.0" used_g="5.0"/>\n  </plate>\n</config>\n')],
    ['Metadata/plate_1.png', Buffer.from('\x89PNG', 'latin1')],
  ];
  const crc32 = (buf: Buffer) => {
    let c, crc = 0xffffffff;
    for (const b of buf) { c = (crc ^ b) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; }
    return (crc ^ 0xffffffff) >>> 0;
  };
  const locals: Buffer[] = []; const centrals: Buffer[] = []; let offset = 0;
  for (const [name, data] of entries) {
    const n = Buffer.from(name); const crc = crc32(data);
    const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(n.length, 26);
    locals.push(local, n, data);
    const central = Buffer.alloc(46); central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(n.length, 28); central.writeUInt32LE(offset, 42);
    centrals.push(central, n);
    offset += 30 + n.length + data.length;
  }
  const dir = Buffer.concat(centrals);
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(dir.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, dir, end]);
}

// --- the mocked world: stateful stand-in for the endpoints these journeys touch --------------------------------------------------------

function mockWorld(): World & { captured: () => { method: string; url: string; body: any }[] } {
  const printers: any[] = [];
  const jobs = new Map<number, { printerId: number; ask: Ask; status: string }>();
  let captured: { method: string; url: string; body: any }[] = [];
  const matches = (slots: Slot[], ask: Ask) => slots.some(s =>
    (!ask.filament_type || s.type === ask.filament_type) && (!ask.filament_subtype || ask.filament_subtype === 'any' || s.subtype === ask.filament_subtype) &&
    (!ask.filament_color || s.color.toLowerCase() === ask.filament_color.toLowerCase()));
  const detailsOf = (id: number) => {
    const j = jobs.get(id)!; const p = printers.find(x => x.id === j.printerId);
    const ok = matches(p.loaded_filaments, j.ask);
    const wanted = [j.ask.filament_type, j.ask.filament_subtype, j.ask.filament_color].filter(Boolean).join(' ');
    return {
      id, uploaded_file_id: 1, plate_number: 1, order_id: null, assigned_printer_id: null, assigned_printer: null, queue_position: 1, status: j.status,
      name: 'Filament journey', block_reason: null, created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
      file: { id: 1, original_filename: 'multi.3mf' }, plate: { plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null },
      model_targets: [],
      printer_configs: [{
        printer_id: p.id, printer_name: p.name, printer_type: 'mock', print_profile: '0.20mm Standard', filament_profile: null,
        filament_manufacturer: j.ask.filament_manufacturer ?? null, filament_type: j.ask.filament_type ?? null,
        filament_subtype: j.ask.filament_subtype ?? null, filament_color: j.ask.filament_color ?? null,
        tool_index: null, filament_map: null, slice_failed: false, slice_error: null, from_model_target: false, low_stock_warning: null,
        filament_match: ok ? { eligible: true, slots: [0], reasons: [] }
          : { eligible: false, slots: [], reasons: p.loaded_filaments.map((s: Slot, i: number) =>
              `T${i}: ${[s.manufacturer, s.type, s.subtype, s.color].filter(Boolean).join(' ')}; wanted ${wanted}`) },
      }],
    };
  };
  return {
    captured: () => captured,
    async attach(page) {
      const base = await mockApi(page, { printers, fleet: () => printers, modelFilaments: [{ index: 1, color: '#FF0000', type: 'PLA' }], respond: (method, path, body) => {
        const m = path.match(/^\/jobs\/(\d+)\/configs$/);
        if (method === 'PATCH' && m) { const j = jobs.get(+m[1])!; const c = body.printer_configs[0]; j.ask = { filament_manufacturer: c.filament_manufacturer, filament_type: c.filament_type, filament_subtype: c.filament_subtype, filament_color: c.filament_color }; return detailsOf(+m[1]); }
        const pm = path.match(/^\/printers\/(\d+)$/);
        if (method === 'PATCH' && pm) { printers.find(p => p.id === +pm[1]).loaded_filaments = body.loaded_filaments; return printers.find(p => p.id === +pm[1]); }
        return undefined;
      } });
      captured = base.captured;
      await page.route('**/api/v1/jobs/*/details', route => {
        const id = +route.request().url().match(/jobs\/(\d+)\/details/)![1];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detailsOf(id)) });
      });
    },
    async printer(slots) {
      const id = printers.length + 1;
      printers.push({ id, name: `Mock ${id}`, printer_type: 'mock', connected: true, state: 'IDLE', machine_profile: 'Mock', machine_profile_reason: null,
        machine_profile_override: null, nozzle_variant: null, model_uuid: 'model-mock', model_label: 'Mock', queue_on: true, enabled: true,
        awaiting_plate_clear: false, progress: 0, remaining_time: 0, temperatures: { nozzle: 25, bed: 25 }, loaded_filaments: slots });
      return id;
    },
    async job(printerId, ask) { const id = jobs.size + 1; jobs.set(id, { printerId, ask, status: 'queued' }); return id; },
    async load(printerId, slots) { printers.find(p => p.id === printerId).loaded_filaments = slots; },
    async jobNow(id) {
      const d = detailsOf(id); const c = d.printer_configs[0];
      const saved = captured.filter(x => x.method === 'PATCH' && x.url.endsWith('/configs')).pop()?.body.printer_configs[0] ?? null;
      return { status: d.status, eligible: c.filament_match.eligible, ask: c, saved };
    },
  };
}

test.describe(`Filament matching (${LIVE ? 'live backend' : 'mocked API'})`, () => {
  let world: World & { captured?: () => unknown };

  test.beforeEach(async ({ page, request }) => {
    world = LIVE ? liveWorld(request) : mockWorld();
    await world.attach(page);
  });

  test('a job nothing matches stays queued, and its detail says why for each slot', async ({ page }) => {
    const printer = await world.printer([RED_PLA, GREEN_PETG]);
    const job = await world.job(printer, SILK_ASK);

    await page.goto(`/jobs/${job}`);

    const why = page.getByTestId('filament-match-reasons');
    await expect(why).toBeVisible();
    await expect(why).toContainText("Filament doesn't match");
    await expect(why).toContainText('T0: Acme PLA standard #FF0000; wanted PLA silk #FF0000');
    await expect(why).toContainText('T1: PETG standard #00FF00');
    await expect(page.getByText('PLA silk #FF0000').first()).toBeVisible();              // the ask, shown as a tuple
    expect((await world.jobNow(job)).status).toBe('queued');                               // never blocked or failed for filament alone
  });

  test('loading the right filament makes the printer match: the reasons are gone and (live) the queue claims the job', async ({ page }) => {
    const printer = await world.printer([RED_PLA, GREEN_PETG]);
    const job = await world.job(printer, SILK_ASK);
    await page.goto(`/jobs/${job}`);
    await expect(page.getByTestId('filament-match-reasons')).toBeVisible();

    await world.load(printer, [RED_PLA, GREEN_PETG, SILK_PLA]);
    await page.reload();

    await expect(page.getByTestId('filament-match-reasons')).toHaveCount(0);
    expect((await world.jobNow(job)).eligible).toBe(true);
    if (LIVE) {
      // The engine skipped the job while it did not match and takes it on the next cycle. (Without a slicer it then blocks for an
      // unrelated reason; the point is that it left "queued" because the filament now matches.)
      await expect.poll(async () => (await world.jobNow(job)).status, { timeout: 15_000 }).not.toBe('queued');
    }
  });

  test('Edit Job shows the stored tuple; changing the subtype and saving sends the whole tuple, and the job then matches', async ({ page }) => {
    const printer = await world.printer([RED_PLA, GREEN_PETG]);
    const job = await world.job(printer, SILK_ASK);

    await page.goto(`/jobs/${job}/edit`);

    await expect(page.getByTestId('filament-ask-type')).toHaveValue('PLA');
    await expect(page.getByTestId('filament-ask-subtype')).toHaveValue('silk');
    await expect(page.getByTestId('filament-match')).toContainText('nothing loaded matches PLA silk');   // the preview agrees with the detail page

    await page.getByTestId('filament-ask-subtype').selectOption('standard');
    await expect(page.getByTestId('filament-match')).toContainText('✓ T0');
    await page.getByRole('button', { name: /Save & re-queue/i }).click();
    await page.waitForURL(new RegExp(`/jobs/${job}$`));

    const now = await world.jobNow(job);
    expect(now.saved).toMatchObject({ filament_type: 'PLA', filament_subtype: 'standard', filament_color: '#FF0000' });
    expect(now.eligible).toBe(true);
    await expect(page.getByTestId('filament-match-reasons')).toHaveCount(0);
  });

  test('"any subtype" and an undefined manufacturer match the plain slot (the wildcard is a value, not a blank)', async ({ page }) => {
    const printer = await world.printer([RED_PLA, GREEN_PETG]);
    const job = await world.job(printer, { filament_type: 'PLA', filament_subtype: 'any' });

    await page.goto(`/jobs/${job}/edit`);
    await expect(page.getByTestId('filament-ask-subtype')).toHaveValue('any');
    await expect(page.getByTestId('filament-ask-manufacturer')).toHaveValue('');
    await expect(page.getByTestId('filament-match')).toContainText('✓ T0');
    expect((await world.jobNow(job)).eligible).toBe(true);
  });
});
