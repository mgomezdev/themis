import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PerPrinterConfig, defaultPerPrinterCfg } from './PerPrinterConfig';
import * as spoolman from '../api/spoolman';

// Mock spoolman hooks so they don't fire real HTTP requests
vi.mock('../api/spoolman', () => ({
  useSpoolmanConfig: vi.fn().mockReturnValue({ config: null, refetch: vi.fn() }),
  useFilaments: vi.fn().mockReturnValue([]),
  filamentDisplayName: vi.fn((f: { vendor?: { name: string }; name: string }) =>
    f.vendor ? `${f.vendor.name} ${f.name}` : f.name),
  parseOrcaProfiles: vi.fn(() => ({})),
}));

// Mock getPrinterProfiles so it doesn't fire real HTTP requests
vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/queue')>();
  return {
    ...actual,
    getPrinterProfiles: vi.fn().mockResolvedValue({
      print_profiles: [],
      filament_profiles: [],
    }),
  };
});

const MULTI = {
  id: 3, name: 'U1', printer_type: 'snapmaker_extended', current_orca_printer_profile: 'U1',
  loaded_filaments: [
    { slot: 0, type: 'PLA', color: '#fff', name: 'PLA', filament_profile: 'PLA @U1' },
    { slot: 1, type: 'PETG', color: '#000', name: 'PETG', filament_profile: 'PETG @U1' },
    { slot: 2, type: 'TPU', color: '#0f0', name: 'TPU', filament_profile: 'TPU @U1' },
  ],
};
const SINGLE = { id: 1, name: 'Mono', printer_type: 'elegoo_centauri', current_orca_printer_profile: 'M', loaded_filaments: [] };

function renderCfg(printer: any, config = defaultPerPrinterCfg(), modelFilaments?: any[]) {
  const onChange = vi.fn();
  render(<PerPrinterConfig printerId={String(printer.id)} printers={[printer as any]} config={config} onChange={onChange} modelFilaments={modelFilaments} />);
  return onChange;
}

const MODEL_FILAMENTS_3 = [
  { index: 1, color: '#ff0000', type: 'PLA' },
  { index: 2, color: '#00ff00', type: 'PETG' },
  { index: 3, color: '#0000ff', type: 'TPU' },
];

describe('PerPrinterConfig', () => {
  it('defaultPerPrinterCfg is all-null (defer)', () => {
    expect(defaultPerPrinterCfg()).toEqual({
      printProfile: null, filamentProfile: null, filamentId: null,
      filamentType: null, filamentColor: null, toolIndex: null, filamentMap: null,
    });
  });

  it('multi-tool: tool select offers Any/default first and writes toolIndex+slot identity', async () => {
    const onChange = renderCfg(MULTI);
    const sel = await screen.findByTestId('tool-select');
    expect((sel as HTMLSelectElement).options[0].textContent).toMatch(/Any \/ default/i);
    fireEvent.change(sel, { target: { value: '2' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ toolIndex: 2, filamentProfile: 'TPU @U1', filamentType: 'TPU' }));
  });

  it('multi-tool: selecting Any/default defers (toolIndex null + cleared ask)', async () => {
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), toolIndex: 1, filamentType: 'PETG' });
    const sel = await screen.findByTestId('tool-select');
    fireEvent.change(sel, { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ toolIndex: null }));
  });

  it('single-tool: defaults to defer (mode select present, ask hidden), Require reveals the ask', async () => {
    renderCfg(SINGLE);
    const mode = await screen.findByTestId('filament-mode');
    expect((mode as HTMLSelectElement).value).toBe('defer');
    expect(screen.queryByTestId('filament-type-input')).toBeNull();
    expect(screen.queryByTestId('filament-catalog-select')).toBeNull();
    fireEvent.change(mode, { target: { value: 'require' } });
    // require mode shows either the catalog select or the manual type input
    expect(screen.queryByTestId('filament-catalog-select') || screen.queryByTestId('filament-type-input')).not.toBeNull();
  });

  it('multi-material: renders three map-tool-* selects when modelFilaments has 3 items', async () => {
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    const sel2 = await screen.findByTestId('map-tool-2');
    const sel3 = await screen.findByTestId('map-tool-3');
    expect(sel1).not.toBeNull();
    expect(sel2).not.toBeNull();
    expect(sel3).not.toBeNull();
    // tool-select (single-tool select) should NOT be present in this mode
    expect(screen.queryByTestId('tool-select')).toBeNull();
  });

  it('multi-material: changing map-tool-2 calls onChange with filamentMap containing {model_filament:2, tool_index:chosen}', async () => {
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel2 = await screen.findByTestId('map-tool-2');
    // MULTI has 3 slots (T0, T1, T2); choose T2 (value=t:2)
    fireEvent.change(sel2, { target: { value: 't:2' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({ model_filament: 2, tool_index: 2, filament_id: null }),
        ]),
      }),
    );
  });

  it('multi-material: single modelFilament still shows Sub-project A tool control', async () => {
    // Only 1 model filament — must NOT show mapping list, must show tool-select instead
    renderCfg(MULTI, defaultPerPrinterCfg(), [{ index: 1, color: '#ff0000', type: 'PLA' }]);
    const toolSel = await screen.findByTestId('tool-select');
    expect(toolSel).not.toBeNull();
    expect(screen.queryByTestId('map-tool-1')).toBeNull();
  });

  it('multi-material: no modelFilaments prop still shows Sub-project A tool control', async () => {
    renderCfg(MULTI);
    const toolSel = await screen.findByTestId('tool-select');
    expect(toolSel).not.toBeNull();
  });
});

const MOCK_FILAMENTS = [
  { id: 7,  name: 'Sky Blue', vendor: { id: 2, name: 'ELEGOO' }, material: 'PLA',  color_hex: '5B9BD5' },
  { id: 19, name: 'White',    vendor: { id: 3, name: 'Sunlu'  }, material: 'PETG', color_hex: 'FFFFFF' },
];

function mockSpoolman(enabled: boolean) {
  vi.mocked(spoolman.useSpoolmanConfig).mockReturnValue(
    enabled
      ? { config: { enabled: true, url: 'http://artemis:7912', api_key: null }, refetch: vi.fn() }
      : { config: null, refetch: vi.fn() },
  );
  vi.mocked(spoolman.useFilaments).mockReturnValue(enabled ? MOCK_FILAMENTS as never : []);
}

describe('PerPrinterConfig — multi-material unified dropdown', () => {
  beforeEach(() => vi.clearAllMocks());

  it('slot-only: renders optgroup "Slots" but no "Catalog" when spoolman off', async () => {
    mockSpoolman(false);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    const html = sel1.innerHTML;
    expect(html).toContain('Slots');
    expect(html).not.toContain('Catalog');
  });

  it('renders "Catalog" optgroup when spoolman is on', async () => {
    mockSpoolman(true);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    expect(sel1.innerHTML).toContain('Catalog');
  });

  it('catalog optgroup contains filament options', async () => {
    mockSpoolman(true);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    const options = Array.from(sel1.querySelectorAll('option')).map(o => o.value);
    expect(options).toContain('f:7');
    expect(options).toContain('f:19');
  });

  it('selecting a catalog filament calls onChange with filament_id, filament_type, filament_color', async () => {
    mockSpoolman(true);
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    fireEvent.change(sel1, { target: { value: 'f:7' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({
            model_filament: 1,
            tool_index: null,
            filament_id: 7,
            filament_type: 'PLA',
            filament_color: '#5B9BD5',
          }),
        ]),
      }),
    );
  });

  it('selecting a slot calls onChange with tool_index set and filament fields null', async () => {
    mockSpoolman(true);
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel2 = await screen.findByTestId('map-tool-2');
    fireEvent.change(sel2, { target: { value: 't:1' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({
            model_filament: 2,
            tool_index: 1,
            filament_id: null,
            filament_type: null,
          }),
        ]),
      }),
    );
  });

  it('amber badge shown when filament_type has no matching loaded slot', async () => {
    mockSpoolman(true);
    const cfgNoMatch = {
      ...defaultPerPrinterCfg(),
      filamentMap: [
        { model_filament: 1, tool_index: null, filament_id: 5, filament_type: 'ABS', filament_color: null },
        { model_filament: 2, tool_index: 0, filament_id: null, filament_type: null, filament_color: null },
        { model_filament: 3, tool_index: 2, filament_id: null, filament_type: null, filament_color: null },
      ],
    };
    renderCfg(MULTI, cfgNoMatch as any, MODEL_FILAMENTS_3);
    const badge = await screen.findByText(/will block at slice/i);
    expect(badge).toBeTruthy();
  });
});
