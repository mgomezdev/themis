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
};

const PRINTER_CONTROLS: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
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

  it('clicking +10 mm calls jogZ with 10', async () => {
    const spy = vi.spyOn(printersApi, 'jogZ').mockResolvedValue(undefined);
    mockFetch([PRINTER_CONTROLS]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge'));
    fireEvent.click(await screen.findByText('+10 mm'));
    expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), 10);
    spy.mockRestore();
  });

  it('clicking −10 mm calls jogZ with -10', async () => {
    const spy = vi.spyOn(printersApi, 'jogZ').mockResolvedValue(undefined);
    mockFetch([PRINTER_CONTROLS]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge'));
    fireEvent.click(await screen.findByText('−10 mm'));
    expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), -10);
    spy.mockRestore();
  });

  it('clicking Light: Off calls setLight with (id, true)', async () => {
    const spy = vi.spyOn(printersApi, 'setLight').mockResolvedValue(undefined);
    mockFetch([PRINTER_CONTROLS]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge'));
    fireEvent.click(await screen.findByText('Light: Off'));
    expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), true);
    spy.mockRestore();
  });
});
