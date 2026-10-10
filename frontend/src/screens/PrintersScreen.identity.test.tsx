import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen, EditForm } from './PrintersScreen';
import type { ApiPrinter, PrinterType } from '../api/printers';
import { printerType, IP_FIELD } from '../test/printerTypes';

const SERIAL = { ...IP_FIELD, name: 'serial_number', label: 'Serial Number' };
const HOSTNAME = { ...IP_FIELD, name: 'hostname', label: 'Hostname Of Box' };

const TYPES: PrinterType[] = [
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S', connection_fields: [IP_FIELD, SERIAL] }),
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'x1c', display_name: 'X1 Carbon', connection_fields: [IP_FIELD, SERIAL] }),
  printerType({ plugin_id: 'moonraker', manufacturer_id: 'voron', manufacturer_name: 'Voron', model_id: 'v2', display_name: '2.4', connection_fields: [HOSTNAME] }),
];

const mk = (over: Partial<ApiPrinter>): ApiPrinter => ({
  id: 1, name: 'Forge', printer_type: 'legacy_string', plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s',
  connection_config: {}, awaiting_plate_clear: false, orca_printer_profiles: [], current_orca_printer_profile: null,
  enabled: true, queue_on: true, connected: true, loaded_filaments: [], build_plate_type: null,
  no_snapshots_while_idle: false, bed_x_mm: 256, bed_y_mm: 256, machine_rate_per_hour: null, quiet_start: null, quiet_end: null,
  ...over,
});

function stubApi(printers: ApiPrinter[], types: PrinterType[] = TYPES) {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const reply = (b: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(b) });
    if (url.includes('/printers/types')) return reply(types);
    if (url === '/api/v1/printers') return reply(printers);
    if (url === '/api/v1/plugins') return reply({ plugins: [], selections: {} });
    if (/\/profiles/.test(url)) return reply({ print_profiles: [], filament_profiles: [] });
    if (url.includes('orca-machine-catalog')) return reply([]);
    return reply({});
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe('PrintersScreen — type label', () => {
  it('shows "<manufacturer_name> <display_name>" of the entry matching (plugin_id, manufacturer_id, model_id)', async () => {
    stubApi([
      mk({ id: 1, name: 'Forge', model_id: 'x1c' }),
      mk({ id: 2, name: 'Vor', plugin_id: 'moonraker', manufacturer_id: 'voron', model_id: 'v2', printer_type: 'legacy_voron' }),
    ]);
    render(<MemoryRouter><PrintersScreen /></MemoryRouter>);
    await screen.findByText('Forge');
    expect(await screen.findByText('Bambu Lab X1 Carbon')).toBeTruthy();
    expect(screen.getByText('Voron 2.4')).toBeTruthy();
    expect(screen.queryByText('legacy_string')).toBeNull();
    expect(screen.queryByText('legacy_voron')).toBeNull();
    expect(screen.queryByText('Bambu Lab P1S')).toBeNull();
  });

  it('falls back to the printer_type string when no types entry matches', async () => {
    stubApi([mk({ name: 'Orphan', model_id: 'gone_model', printer_type: 'legacy_string' })]);
    render(<MemoryRouter><PrintersScreen /></MemoryRouter>);
    await screen.findByText('Orphan');
    await waitFor(() => expect(screen.getByText('legacy_string')).toBeTruthy());
    expect(screen.queryByText(/Bambu Lab/)).toBeNull();
  });

  it('does not match on model_id alone: same model under a different plugin falls back', async () => {
    stubApi([mk({ name: 'Other', plugin_id: 'other_plugin', printer_type: 'legacy_string' })]);
    render(<MemoryRouter><PrintersScreen /></MemoryRouter>);
    await screen.findByText('Other');
    await waitFor(() => expect(screen.getByText('legacy_string')).toBeTruthy());
    expect(screen.queryByText('Bambu Lab P1S')).toBeNull();
  });
});

describe('EditForm — connection fields by plugin', () => {
  const renderEdit = (printer: ApiPrinter) => {
    stubApi([printer]);
    return render(<MemoryRouter><EditForm printer={printer} types={TYPES} onSave={() => {}} onCancel={() => {}} /></MemoryRouter>);
  };

  it('shows the connection fields of the entry matching the printer\'s plugin_id', async () => {
    renderEdit(mk({ plugin_id: 'moonraker', manufacturer_id: 'voron', model_id: 'v2', connection_config: { hostname: 'voron.local' } }));
    expect(await screen.findByText(/Hostname Of Box/)).toBeTruthy();
    expect(screen.queryByText(/Serial Number/)).toBeNull();
    expect(screen.queryByText(/IP Address/)).toBeNull();
  });

  it('shows the bambu fields for a bambu printer', async () => {
    renderEdit(mk({ plugin_id: 'bambu', connection_config: { ip_address: '10.0.0.4', serial_number: 'S1' } }));
    expect(await screen.findByText(/Serial Number/)).toBeTruthy();
    expect(screen.getByText(/IP Address/)).toBeTruthy();
    expect(screen.queryByText(/Hostname Of Box/)).toBeNull();
  });
});
