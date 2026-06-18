import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FleetScreen } from './FleetScreen';
import type { FleetPrinter } from '../api/fleet';
import * as printersApi from '../api/printers';

// ── Mock WebSocket ──────────────────────────────────────────────────────────
class MockWS {
  static instances: MockWS[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor() {
    MockWS.instances.push(this);
  }
}

// ── Fixtures ────────────────────────────────────────────────────────────────
const PRINTER_1: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
  queue_on: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Bambu PA-CF', type: 'PA-CF', color: '#0c0c0c' }],
  state: 'RUNNING',
  progress: 28,
  remaining_time: 312,
  layer_num: 88,
  total_layers: 312,
  temperatures: { nozzle: 285, bed: 95, chamber: 58 },
  capabilities: {},
  current_print: 'arm_bracket.gcode',
  fan_model: 0,
  fan_aux: 0,
  fan_box: 0,
};

const PRINTER_CONTROLS: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
  queue_on: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'PA-CF', type: 'PA-CF', color: '#0c0c0c' }],
  state: 'RUNNING',
  progress: 28,
  remaining_time: 312,
  layer_num: 88,
  total_layers: 312,
  temperatures: { nozzle: 285, bed: 95, chamber: 58 },
  capabilities: {
    pause_resume: true,
    chamber_light: true,
    fan_control: true,
    temp_control: true,
  },
  current_print: 'arm_bracket.gcode',
  fan_model: 80,
  fan_aux: 60,
  fan_box: 40,
};

function mockFetch(data: FleetPrinter[]) {
  vi.stubGlobal('fetch', vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(data) }),
  ));
}

describe('FleetScreen', () => {
  beforeEach(() => {
    MockWS.instances = [];
    vi.stubGlobal('WebSocket', MockWS);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows printer name loaded from API', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());
  });

  it('shows correct printer count in header', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText(/1 printers online/i)).toBeInTheDocument());
  });

  it('shows 0 printers when API returns empty list', async () => {
    mockFetch([]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText(/0 printers online/i)).toBeInTheDocument());
  });

  it('reflects WebSocket printer_state update', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());

    act(() => {
      MockWS.instances[0].onmessage?.({
        data: JSON.stringify({
          type: 'printer_state',
          data: { ...PRINTER_1, state: 'IDLE', progress: 0, remaining_time: 0 },
        }),
      });
    });

    // After update to IDLE, timeRemaining becomes 0 — verify no crash and Forge still shows
    expect(screen.getByText('Forge')).toBeInTheDocument();
  });

  it('ignores non-printer_state WebSocket events', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());

    act(() => {
      MockWS.instances[0].onmessage?.({
        data: JSON.stringify({ type: 'plate_clear_required', data: { printer_id: 1 } }),
      });
    });

    // Component should still render without crashing
    expect(screen.getByText('Forge')).toBeInTheDocument();
  });

  it('closes WebSocket on unmount', async () => {
    mockFetch([PRINTER_1]);
    const { unmount } = render(<FleetScreen />);
    await waitFor(() => expect(MockWS.instances.length).toBe(1));
    unmount();
    expect(MockWS.instances[0].close).toHaveBeenCalled();
  });

  it('clicking Pause calls pausePrinter with printer id', async () => {
    const spy = vi.spyOn(printersApi, 'pausePrinter').mockResolvedValue(undefined);
    mockFetch([PRINTER_CONTROLS]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge'));
    fireEvent.click(await screen.findByText('Pause'));
    expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id));
    spy.mockRestore();
  });

  it('clicking Stop calls stopPrinter', async () => {
    const spy = vi.spyOn(printersApi, 'stopPrinter').mockResolvedValue(undefined);
    mockFetch([PRINTER_CONTROLS]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge'));
    fireEvent.click(await screen.findByText('Stop'));
    expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id));
    spy.mockRestore();
  });

});

// ── Integration: FilamentPicker + SlotSpoolPicker spool selection ─────────────

import userEvent from '@testing-library/user-event';

const SPOOL_INTEGRATION: FleetPrinter = {
  id: 2,
  name: 'Atlas',
  printer_type: 'elegoo_centauri',
  enabled: true,
  queue_on: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', spoolman_spool_id: null }],
  state: 'IDLE',
  progress: 0,
  remaining_time: 0,
  layer_num: null,
  total_layers: null,
  temperatures: { nozzle: 25, bed: 25 },
  capabilities: {},
  current_print: null,
  fan_model: 0,
  fan_aux: 0,
  fan_box: 0,
};

const MOCK_SPOOL = {
  id: 3,
  remaining_weight: 500,
  used_weight: 0,
  filament: { id: 20, vendor: { name: 'ELEGOO' }, name: 'Space Grey PLA', material: 'PLA', color_hex: 'AAAAAA' },
};

const MOCK_API_PRINTER = {
  id: 2, name: 'Atlas', printer_type: 'elegoo_centauri', enabled: true,
  queue_on: false, awaiting_plate_clear: false, connected: true,
  current_orca_printer_profile: null,
  orca_printer_profiles: [],
  connection_config: { host: '10.0.0.1' },
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', filament_profile: null, spoolman_spool_id: null }],
};

function makeIntegrationFetch(
  capturedCalls: Array<[string, RequestInit | undefined]>,
) {
  return vi.fn((url: string, init?: RequestInit) => {
    capturedCalls.push([url, init]);
    // Fleet list
    if (url === '/api/v1/fleet') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([SPOOL_INTEGRATION]) });
    }
    // Spoolman config
    if (url === '/api/v1/settings/spoolman') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: true, url: 'http://spoolman.local', api_key: null }) });
    }
    // Spoolman spools
    if (url === '/api/v1/spoolman/spools') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([MOCK_SPOOL]) });
    }
    // Spoolman filaments
    if (url === '/api/v1/spoolman/filaments') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    // Printer types
    if (url.includes('/api/v1/printers/types')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    // Machine catalog
    if (url.includes('orca-machine-catalog')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    // Printer profiles
    if (url.match(/\/api\/v1\/printers\/\d+\/profiles/)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ print_profiles: [], filament_profiles: [] }) });
    }
    // Individual printer fetch
    if (url.match(/\/api\/v1\/printers\/\d+/) && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_API_PRINTER) });
    }
    // PATCH save
    if (url.match(/\/api\/v1\/printers\/\d+/) && init?.method === 'PATCH') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_API_PRINTER) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
}

describe('FleetScreen FilamentPicker + SlotSpoolPicker integration', () => {
  let calls: Array<[string, RequestInit | undefined]>;

  beforeEach(() => {
    calls = [];
    MockWS.instances = [];
    vi.stubGlobal('WebSocket', MockWS);
    vi.stubGlobal('fetch', makeIntegrationFetch(calls));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('saves correct spool fields when a spool is selected in FilamentPicker', async () => {
    const user = userEvent.setup();
    render(<FleetScreen />);

    // Wait for the printer tile to appear and click to expand it
    fireEvent.click(await screen.findByText('Atlas'));

    // Click the "Change" button to open FilamentPicker
    const changeBtn = await screen.findByRole('button', { name: /change/i });
    await user.click(changeBtn);

    // Wait for the spool combobox to appear (FilamentPicker fetches printer data)
    const searchInput = await screen.findByPlaceholderText('Search spools…');

    // Focus to open dropdown, then pick the spool via mouseDown
    fireEvent.focus(searchInput);
    const spoolOption = await screen.findByText(/#3 ELEGOO Space Grey PLA PLA/);
    fireEvent.mouseDown(spoolOption);

    // Click "Save filaments"
    await user.click(screen.getByRole('button', { name: /save filaments/i }));

    // Verify the PATCH call contains the correct spool fields
    const patchCall = calls.find(([url, init]) => url.match(/\/api\/v1\/printers\/\d+/) && init?.method === 'PATCH');
    expect(patchCall).toBeTruthy();
    const body = JSON.parse(patchCall![1]!.body as string);
    const slot = body.loaded_filaments[0];
    expect(slot.spoolman_spool_id).toBe('3');
    expect(slot.type).toBe('PLA');
    expect(slot.color).toBe('#AAAAAA');
  });
});
