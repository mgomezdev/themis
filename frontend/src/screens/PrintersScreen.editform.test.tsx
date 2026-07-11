import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EditForm } from './PrintersScreen';
import type { ApiPrinter, PrinterType } from '../api/printers';

const TYPES: PrinterType[] = [
  { printer_type: 'bambu', display_name: 'Bambu', connection_fields: [] },
];
const PRINTER: ApiPrinter = {
  id: 7, name: 'Iris', printer_type: 'bambu', connection_config: {},
  awaiting_plate_clear: false, orca_printer_profiles: [], current_orca_printer_profile: 'Bambu Lab P1S 0.4 nozzle',
  enabled: true, queue_on: true, connected: true,
  loaded_filaments: [{ slot: 0, filament_id: 'GFL99', name: 'PLA', type: 'PLA', color: '#fff' }],
  build_plate_type: null,
  no_snapshots_while_idle: false,
  bed_x_mm: 256,
  bed_y_mm: 256,
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/orca-machine-catalog')) return new Response('[]', { status: 200 });
    if (url.includes('/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
    if (url.match(/\/printers\/7\/profiles$/)) return new Response(JSON.stringify({ print_profiles: [], filament_profiles: ['Generic PLA @BBL', 'PolyTerra PLA @BBL'] }), { status: 200 });
    if (url.match(/\/printers\/7$/) && init?.method === 'PATCH') return new Response(JSON.stringify(PRINTER), { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('EditForm per-tray mapping', () => {
  it('saves a chosen filament_profile on the slot', async () => {
    const fetchMock = vi.mocked(fetch);
    render(<EditForm printer={PRINTER} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    // filament-profile select for slot 0 populated from /profiles
    const sel = await screen.findByLabelText('Filament profile for slot 1');
    fireEvent.change(sel, { target: { value: 'Generic PLA @BBL' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(c => /\/printers\/7$/.test(c[0] as string) && (c[1] as RequestInit)?.method === 'PATCH');
      expect(patch).toBeTruthy();
      const body = JSON.parse((patch![1] as RequestInit).body as string);
      expect(body.loaded_filaments[0].filament_profile).toBe('Generic PLA @BBL');
    });
  });
});
