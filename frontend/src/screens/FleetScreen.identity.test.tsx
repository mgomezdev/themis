import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FleetScreen } from './FleetScreen';
import type { FleetPrinter } from '../api/fleet';
import type { PrinterType } from '../api/printers';
import { printerType, IP_FIELD } from '../test/printerTypes';

class MockWS { onmessage = null; close = vi.fn(); }

const HOSTNAME = { ...IP_FIELD, name: 'hostname', label: 'Hostname Of Box' };
const SERIAL = { ...IP_FIELD, name: 'serial_number', label: 'Serial Number' };

const TYPES: PrinterType[] = [
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S', connection_fields: [IP_FIELD, SERIAL] }),
  printerType({ plugin_id: 'moonraker', manufacturer_id: 'voron', manufacturer_name: 'Voron', model_id: 'v2', display_name: '2.4', connection_fields: [HOSTNAME] }),
];

const FLEET_PRINTER: FleetPrinter = {
  id: 2, name: 'Atlas', printer_type: 'legacy_voron', plugin_id: 'moonraker',
  enabled: true, queue_on: true, connected: true, awaiting_plate_clear: false, no_snapshots_while_idle: false,
  loaded_filaments: [], state: 'IDLE', progress: 0, remaining_time: 0, layer_num: null, total_layers: null,
  temperatures: { nozzle: 25, bed: 25 }, capabilities: {}, current_print: null, fan_model: 0, fan_aux: 0, fan_box: 0,
};

const API_PRINTER = {
  id: 2, name: 'Atlas', printer_type: 'legacy_voron', plugin_id: 'moonraker', manufacturer_id: 'voron', model_id: 'v2',
  enabled: true, queue_on: true, awaiting_plate_clear: false, no_snapshots_while_idle: false, connected: true,
  current_orca_printer_profile: null, orca_printer_profiles: [], connection_config: { hostname: 'voron.local' },
  loaded_filaments: [], machine_rate_per_hour: null, quiet_start: null, quiet_end: null,
};

afterEach(() => vi.unstubAllGlobals());

describe('FleetScreen — edit panel connection by plugin', () => {
  it('renders the plugin\'s connection fields and tests the connection with printer_type = plugin id', async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal('WebSocket', MockWS);
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      const reply = (b: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(b) });
      if (url === '/api/v1/fleet') return reply([FLEET_PRINTER]);
      if (url === '/api/v1/maintenance/status') return reply([]);
      if (url.includes('/printers/types')) return reply(TYPES);
      if (url.includes('orca-machine-catalog')) return reply([]);
      if (url === '/api/v1/printers/test-connection') return reply({ ok: true });
      if (/\/api\/v1\/printers\/\d+$/.test(url) && !init?.method) return reply(API_PRINTER);
      return reply({});
    }));
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Atlas'));
    fireEvent.click(await screen.findByTitle('Edit printer'));

    expect(await screen.findByText(/Hostname Of Box/)).toBeTruthy();
    expect(screen.queryByText(/Serial Number/)).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: /test connection/i }));
    await waitFor(() => expect(calls.some(([u]) => u === '/api/v1/printers/test-connection')).toBe(true));
    const [, init] = calls.find(([u]) => u === '/api/v1/printers/test-connection')!;
    const body = JSON.parse(init!.body as string);
    expect(body.printer_type).toBe('moonraker');
    expect(body.printer_type).not.toBe('legacy_voron');
  });
});
