import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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
  enabled: true, queue_on: true, configured: true, config_error: null, connected: true,
  loaded_filaments: [{ slot: 0, filament_id: 'GFL99', name: 'PLA', type: 'PLA', color: '#fff' }],
  build_plate_type: null,
  no_snapshots_while_idle: false,
  bed_x_mm: 256,
  bed_y_mm: 256,
  machine_rate_per_hour: null,
  preferred_slicer_plugin_id: null, slice_overrides: {},
  default_process_profile: null,
  single_colour_only: false, bed_z_mm: null, waiver_flag_at: 0, waiver_stop_at: 0, test_print_job_id: null,
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
    if (url.match(/\/printers\/7\/profiles$/)) return new Response(JSON.stringify({ print_profiles: ['0.20mm Standard @BBL', '0.12mm Fine @BBL'], filament_profiles: ['Generic PLA @BBL', 'PolyTerra PLA @BBL'] }), { status: 200 });
    if (url === '/api/v1/jobs/override-settings') return new Response(JSON.stringify([
      { key: 'enable_support', label: 'Enable supports', kind: 'bool', options: ['0', '1'] },
      { key: 'brim_type', label: 'Brim type', kind: 'enum', options: ['auto_brim', 'outer_only', 'no_brim'] },
    ]), { status: 200 });
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
    await waitFor(() => expect([...select.options].map(o => o.textContent)).toEqual(['Model default (0.4mm)', '0.4mm · Bambu Lab P1S 0.4 nozzle', '0.6mm · Bambu Lab P1S 0.6 nozzle']));
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

  it('switches the available mappings to the chosen slicer and saves a high-flow override for this printer', async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/v1/plugins') return Response.json({
        plugins: ['laminus', 'alternate'].map(id => ({ id, name: id, enabled: true, provides: [{ capability: 'gcode.produce' }] })),
        selections: { 'gcode.produce': 'alternate' },
      });
      if (url.endsWith('/slicer-profiles')) return Response.json([
        ...ROWS, { ...row(3, 0.4, 'high_flow', 'Alt high-flow', true), provider_plugin_id: 'alternate' },
      ]);
      return base(url, init);
    }));
    render(<EditForm printer={{ ...PRINTER, slicer_plugin_id: 'laminus', model_slicer_plugin_id: 'laminus' }}
                     types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const slicer = await screen.findByLabelText('Slice jobs for this printer with');
    await screen.findByRole('option', { name: /Model default slicer \(laminus\)/ });
    expect(screen.queryByRole('option', { name: /Alt high-flow/ })).toBeNull();
    fireEvent.change(slicer, { target: { value: 'alternate' } });
    expect(await screen.findByRole('option', { name: '0.4mm high-flow · Alt high-flow' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /0.6mm · Bambu Lab/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('Nozzle fitted'), { target: { value: '0.4|high_flow' } });
    expect(screen.getByText('Alt high-flow')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toMatchObject({
      preferred_slicer_plugin_id: 'alternate', nozzle_variant: { diameter_mm: 0.4, flow: 'high_flow' },
    }));
  });

  it('keeps a custom machine profile on the slicer it was entered for', async () => {
    const printer = { ...PRINTER, slicer_plugin_id: 'laminus', model_slicer_plugin_id: 'laminus' };
    render(<EditForm printer={printer} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByText('Machine profile override'));
    fireEvent.change(screen.getByLabelText('Custom machine profile'), { target: { value: 'P1S High Flow Lab' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toMatchObject({
      preferred_slicer_plugin_id: 'laminus', machine_profile_override: 'P1S High Flow Lab',
    }));
  });
});

describe('EditForm slicing defaults (BIZ-358)', () => {
  it('saves a default process profile from the printer’s compatible presets and the single-colour flag', async () => {
    render(<EditForm printer={PRINTER} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const select = await screen.findByLabelText('Default process profile') as HTMLSelectElement;
    await waitFor(() => expect([...select.options].map(o => o.textContent)).toEqual(['— none —', '0.20mm Standard @BBL', '0.12mm Fine @BBL']));
    fireEvent.change(select, { target: { value: '0.12mm Fine @BBL' } });
    fireEvent.click(screen.getByLabelText(/Single colour only/));
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().default_process_profile).toBe('0.12mm Fine @BBL');
    expect(patchBody().single_colour_only).toBe(true);
  });

  it('shows the stored values and clears the default to null', async () => {
    render(<EditForm printer={{ ...PRINTER, default_process_profile: '0.20mm Standard @BBL', single_colour_only: true }}
                     types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const select = await screen.findByLabelText('Default process profile') as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('0.20mm Standard @BBL'));
    expect((screen.getByLabelText(/Single colour only/) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(select, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().default_process_profile).toBeNull();
  });
});

describe('EditForm build height (BIZ-363)', () => {
  it('saves a build height, and blank (or not a positive number) clears it to the profile height', async () => {
    render(<EditForm printer={{ ...PRINTER, bed_z_mm: 250 }} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const z = screen.getByLabelText('Build height (mm)') as HTMLInputElement;
    expect(z.value).toBe('250');
    fireEvent.change(z, { target: { value: '320' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().bed_z_mm).toBe(320);
  });

  it.each([[''], ['0'], ['-5']])('sends null for %j', async (value) => {
    render(<EditForm printer={{ ...PRINTER, bed_z_mm: 250 }} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText('Build height (mm)'), { target: { value } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()).toBeTruthy());
    expect(patchBody().bed_z_mm).toBeNull();
  });
});

describe('EditForm printer setting overrides', () => {
  it('saves a brim type picked for the printer and keeps the ones it already had', async () => {
    const { resetOverrideSettingsCache } = await import('../components/overrideSettings');
    resetOverrideSettingsCache();
    render(<EditForm printer={{ ...PRINTER, slice_overrides: { enable_support: '0' } }} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    const scope = await screen.findByTestId('printer-overrides');
    fireEvent.change(within(scope).getByLabelText('Add override'), { target: { value: 'brim_type' } });
    fireEvent.change(within(scope).getByLabelText('Brim type'), { target: { value: 'outer_only' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(patchBody()?.slice_overrides).toEqual({ enable_support: '0', brim_type: 'outer_only' }));
  });
});
