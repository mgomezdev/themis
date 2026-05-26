import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FleetScreen } from './FleetScreen';
import type { FleetPrinter } from '../api/fleet';

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
});
