import type { Page, Route } from '@playwright/test';

export interface Mocks { captured: { url: string; method: string; body: any }[]; }

const U1 = {
  id: 3, name: 'U1', printer_type: 'snapmaker_extended', connected: true, state: 'IDLE',
  current_orca_printer_profile: 'Snapmaker U1 (0.4 nozzle)', queue_on: true, enabled: true,
  awaiting_plate_clear: false, progress: 0, remaining_time: 0,
  temperatures: { nozzle: 27, bed: 25, extruders: [{index:0,temp:27},{index:1,temp:27},{index:2,temp:28},{index:3,temp:28}] },
  loaded_filaments: [
    { slot: 0, filament_id: null, name: 'PLA White', type: 'PLA', color: '#ffffff', filament_profile: 'Generic PLA @System' },
    { slot: 1, filament_id: null, name: 'PETG Black', type: 'PETG', color: '#000000', filament_profile: 'Generic PETG @System' },
    { slot: 2, filament_id: null, name: 'TPU Green', type: 'TPU', color: '#00ff00', filament_profile: 'Generic TPU @System' },
    { slot: 3, filament_id: null, name: 'PLA Blue', type: 'PLA', color: '#0000ff', filament_profile: 'Generic PLA @System' },
  ],
};
const MONO = {
  id: 1, name: 'Mono', printer_type: 'elegoo_centauri', connected: true, state: 'IDLE',
  current_orca_printer_profile: 'Mono', queue_on: true, enabled: true, awaiting_plate_clear: false,
  progress: 0, remaining_time: 0, temperatures: { nozzle: 25, bed: 25 }, loaded_filaments: [],
};
const PROFILES = { print_profiles: ['0.20mm Standard', '0.08 Extra Fine'], filament_profiles: ['Generic PLA @System', 'Generic PETG @System', 'Generic TPU @System'] };
const FILE = { id: 1, original_filename: 'multi.3mf', folder: '/', plate_count: 2 };
const PLATES = [
  { plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null },
  { plate_number: 2, estimated_time: 1800, filament_g: 6, thumbnail_path: null },
];
const MODEL_FILAMENTS = [{ index: 1, color: '#F78E0E', type: 'PLA' }, { index: 2, color: '#003776', type: 'PLA' }];

type Json = (route: Route, body?: any) => Promise<void>;
const ok: Json = (route, body = {}) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

export async function mockApi(page: Page, over: Partial<{
  printers: any[]; fleet: any[]; profiles: any; files: any[]; plates: any[]; modelFilaments: any[]; jobDetails: any;
}> = {}): Promise<Mocks> {
  const printers = over.printers ?? [MONO, U1];
  const fleet = over.fleet ?? [MONO, U1];
  const profiles = over.profiles ?? PROFILES;
  const files = over.files ?? [FILE];
  const plates = over.plates ?? PLATES;
  const modelFilaments = over.modelFilaments ?? MODEL_FILAMENTS;
  const mocks: Mocks = { captured: [] };

  // Mock WebSocket for real-time updates (prevents hanging on ws connection).
  // Must use addInitScript so the mock is installed before the page scripts run
  // after navigation — evaluateHandle only runs in the pre-navigation context.
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

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/v1/, '');
    const method = req.method();

    if (method !== 'GET') {
      let body: any = null;
      try { body = req.postDataJSON(); } catch { /* no body */ }
      mocks.captured.push({ url: path, method, body });
      return ok(route, { id: 123, status: 'queued' });
    }
    if (path === '/printers' || path === '/printers/') return ok(route, printers);
    if (path === '/printers/types') return ok(route, []);
    if (path === '/fleet') return ok(route, fleet);
    let m;
    if ((m = path.match(/^\/printers\/(\d+)\/profiles$/))) return ok(route, profiles);
    if ((m = path.match(/^\/printers\/(\d+)$/))) return ok(route, printers.find(p => p.id === +m[1]) ?? {});
    if (path === '/files') return ok(route, files);
    if ((m = path.match(/^\/files\/(\d+)\/plates$/))) return ok(route, plates);
    if ((m = path.match(/^\/files\/(\d+)\/model-filaments$/))) return ok(route, modelFilaments);
    if (path === '/settings/spoolman') return ok(route, { enabled: false });
    if (path === '/spoolman/filaments' || path === '/spoolman/spools') return ok(route, []);
    if ((m = path.match(/^\/jobs\/(\d+)\/details$/)) || (m = path.match(/^\/jobs\/(\d+)$/)))
      return over.jobDetails ? ok(route, over.jobDetails) : ok(route, {});
    if (path === '/queue/config' || path === '/settings/queue') return ok(route, { check_interval_minutes: 5 });
    if (path === '/queue' || path === '/jobs') return ok(route, []);
    if (path === '/orders') return ok(route, []);
    if (path === '/machine-catalog') return ok(route, []);
    return ok(route, {});  // permissive default for any unlisted GET
  });
  return mocks;
}
