import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EditForm } from './PrintersScreen';
import type { ApiPrinter, PrinterType } from '../api/printers';
import { printerType } from '../test/printerTypes';

const TYPES: PrinterType[] = [
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S' }),
];
const PRINTER: ApiPrinter = {
  id: 7, name: 'Iris', printer_type: 'bambu', plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s', model_uuid: 'model-p1s', connection_config: {},
  awaiting_plate_clear: false, orca_printer_profiles: [], machine_profile_override: null,
  nozzle_variant: null, machine_profile: 'Bambu Lab P1S 0.4 nozzle', machine_profile_reason: null, model_label: 'Bambu Lab P1S',
  enabled: true, queue_on: true, connected: true,
  loaded_filaments: [{ slot: 0, filament_id: 'GFL99', name: 'PLA', type: 'PLA', color: '#fff' }],
  build_plate_type: null,
  no_snapshots_while_idle: false,
  bed_x_mm: 256,
  bed_y_mm: 256,
  machine_rate_per_hour: null,
  preferred_slicer_plugin_id: null,
  quiet_start: null,
  quiet_end: null,
};

const row = (id: number, d: number, flow: string, profile: string, is_default = false) =>
  ({ id, model_uuid: 'model-p1s', provider_plugin_id: 'laminus', nozzle_diameter_mm: d, flow, machine_profile: profile, is_default });
const ROWS = [row(1, 0.4, 'standard', 'Bambu Lab P1S 0.4 nozzle', true), row(2, 0.6, 'standard', 'Bambu Lab P1S 0.6 nozzle')];

const patchBody = () => {
  const patch = vi.mocked(fetch).mock.calls.find(c => /\/printers\/7$/.test(c[0] as string) && (c[1] as RequestInit)?.method === 'PATCH');
  return patch ? JSON.parse((patch[1] as RequestInit).body as string) : null;
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/orca-machine-catalog')) return new Response('[]', { status: 200 });
    if (url === '/api/v1/printer-models/model-p1s/slicer-profiles') return new Response(JSON.stringify(ROWS), { status: 200 });
    if (url === '/api/v1/plugins') return new Response(JSON.stringify({ plugins: [], selections: {} }), { status: 200 });
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

describe('EditForm slot conflicts (filament matching)', () => {
  const bound = { ...PRINTER, loaded_filaments: [
    { slot: 0, filament_id: null, name: 'PLA', type: 'PLA', subtype: 'standard', color: '#fff', reported: true, inventory: { provider: 'spoolman', spool_ref: '9' } },
    { slot: 1, filament_id: null, name: 'PETG', type: 'PETG', subtype: 'standard', color: '#000', reported: true, inventory: { provider: 'spoolman', spool_ref: '4' } },
  ] };

  it('flags exactly the slots the server reports as in conflict with their bound spool', async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) =>
      url === '/api/v1/printers/7/slot-conflicts' ? new Response(JSON.stringify({ conflicts: [1] }), { status: 200 }) : base(url, init)));
    render(<EditForm printer={bound} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const flags = await screen.findAllByTestId('slot-conflict');
    expect(flags).toHaveLength(1);
    expect(flags[0].closest('.card')?.textContent).toMatch(/Slot 2/);              // slot index 1 = "Slot 2"
  });

  it('shows no flag when the server reports no conflicts', async () => {
    render(<EditForm printer={bound} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    await screen.findByLabelText('Filament profile for slot 1');
    expect(screen.queryByTestId('slot-conflict')).toBeNull();
  });
});

describe('EditForm nozzle variant (BIZ-294)', () => {
  it('replaces the per-printer make/model picker with the nozzles its model has a profile for, and saves the chosen one', async () => {
    render(<EditForm printer={PRINTER} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const select = await screen.findByLabelText('Nozzle fitted') as HTMLSelectElement;
    await waitFor(() => expect([...select.options].map(o => o.textContent)).toEqual(['Model default (0.4mm)', '0.4mm', '0.6mm']));
    expect(screen.queryByLabelText('Make')).toBeNull();

    fireEvent.change(select, { target: { value: '0.6|standard' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().nozzle_variant).toEqual({ diameter_mm: 0.6, flow: 'standard' });
    expect(patchBody()).not.toHaveProperty('machine_profile_override');
    expect(patchBody()).not.toHaveProperty('orca_printer_profiles');
  });

  it('keeps the printer on the model default (null) when no nozzle is chosen', async () => {
    render(<EditForm printer={PRINTER} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    await screen.findByLabelText('Nozzle fitted');
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().nozzle_variant).toBeNull();
  });
});
