import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PerPrinterConfig, defaultPerPrinterCfg } from './PerPrinterConfig';
import * as inventoryApi from '../api/inventory';
import { activeInventory, mkMaterial, noInventory } from '../test/inventoryFixtures';

// Mock the inventory hooks so they don't fire real HTTP requests
vi.mock('../api/inventory', async importOriginal => ({
  ...(await importOriginal<typeof import('../api/inventory')>()),
  useInventory: vi.fn(() => ({ plugin: null, id: null, has: () => false })),
  useMaterials: vi.fn(() => []),
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
  id: 3, name: 'U1', printer_type: 'snapmaker_extended', machine_profile: 'U1',
  loaded_filaments: [
    { slot: 0, type: 'PLA', subtype: 'standard', manufacturer: 'Acme', color: '#ffffff', name: 'PLA', filament_profile: 'PLA @U1' },
    { slot: 1, type: 'PETG', subtype: 'standard', manufacturer: null, color: '#000000', name: 'PETG', filament_profile: 'PETG @U1' },
    { slot: 2, type: 'TPU', subtype: 'standard', manufacturer: null, color: '#00ff00', name: 'TPU', filament_profile: 'TPU @U1' },
  ],
};
const SINGLE = { id: 1, name: 'Mono', printer_type: 'elegoo_centauri', machine_profile: 'M', loaded_filaments: [] };

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

const NO_ASK_CFG = { filamentManufacturer: null, filamentType: null, filamentSubtype: null, filamentColor: null };

beforeEach(() => {
  vi.mocked(inventoryApi.useInventory).mockReturnValue(noInventory());
  vi.mocked(inventoryApi.useMaterials).mockReturnValue([]);
});

describe('PerPrinterConfig', () => {
  it('defaultPerPrinterCfg is the undefined ask (defer): every part null, no material reference', () => {
    expect(defaultPerPrinterCfg()).toEqual({
      printProfile: null, filamentProfile: null, ...NO_ASK_CFG, toolIndex: null, filamentMap: null,
    });
  });

  it('multi-tool: tool select offers "any tool" first; pinning a tool takes its profile and clears the ask', async () => {
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), filamentType: 'PLA', filamentSubtype: 'any' });
    const sel = await screen.findByTestId('tool-select');
    expect((sel as HTMLSelectElement).options[0].textContent).toMatch(/Any tool/i);
    fireEvent.change(sel, { target: { value: '2' } });
    expect(onChange).toHaveBeenCalledWith({ toolIndex: 2, filamentProfile: 'TPU @U1', ...NO_ASK_CFG });   // a pinned tool is not matched
  });

  it('multi-tool: choosing "any tool" goes back to matching by filament', async () => {
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), toolIndex: 1 });
    expect(screen.queryByTestId('filament-ask-type')).toBeNull();           // pinned: nothing to match
    fireEvent.change(await screen.findByTestId('tool-select'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith({ toolIndex: null });
  });

  it('multi-tool without a pin edits the tuple: choosing a type gives subtype any, and says which tool would print', async () => {
    const onChange = renderCfg(MULTI);
    fireEvent.change(await screen.findByTestId('filament-ask-type'), { target: { value: 'PETG' } });
    expect(onChange).toHaveBeenCalledWith({ ...NO_ASK_CFG, filamentType: 'PETG', filamentSubtype: 'any' });
  });

  it('single-slot printer: the ask starts undefined ("any loaded filament") and is edited as a tuple', async () => {
    const onChange = renderCfg(SINGLE);
    expect(await screen.findByTestId('filament-match')).toHaveTextContent('Any loaded filament');
    fireEvent.change(screen.getByTestId('filament-ask-manufacturer'), { target: { value: 'Acme' } });
    expect(onChange).toHaveBeenCalledWith({ ...NO_ASK_CFG, filamentManufacturer: 'Acme' });
  });

  it('the chip skips a slot the server reports in conflict with its bound spool, as the queue does', async () => {
    const stubConflicts = (conflicts: number[]) => vi.stubGlobal('fetch', vi.fn(async (url: string) =>
      new Response(JSON.stringify(url.endsWith('/slot-conflicts') ? { conflicts } : []), { status: 200 })));
    const config = { ...defaultPerPrinterCfg(), filamentType: 'PLA', filamentSubtype: 'any' };
    stubConflicts([0]);
    const { unmount } = render(<PerPrinterConfig printerId="3" printers={[MULTI as any]} onChange={vi.fn()} config={config} />);
    await waitFor(() => expect(screen.getByTestId('filament-match')).toHaveTextContent(/nothing loaded matches PLA/));
    unmount();
    stubConflicts([]);
    render(<PerPrinterConfig printerId="3" printers={[MULTI as any]} onChange={vi.fn()} config={config} />);
    await waitFor(() => expect(screen.getByTestId('filament-match')).toHaveTextContent('✓ T0'));
  });

  it('the chip names the lowest matching slot using the whole tuple, and warns (job waits) when nothing matches', async () => {
    const cfg = (over: object) => ({ ...defaultPerPrinterCfg(), ...over });
    const { unmount } = render(<PerPrinterConfig printerId="3" printers={[MULTI as any]} onChange={vi.fn()}
      config={cfg({ filamentType: 'PLA', filamentSubtype: 'standard', filamentColor: '#FFFFFF', filamentManufacturer: 'acme' })} />);
    expect(await screen.findByTestId('filament-match')).toHaveTextContent('✓ T0');
    unmount();
    render(<PerPrinterConfig printerId="3" printers={[MULTI as any]} onChange={vi.fn()}
      config={cfg({ filamentType: 'PLA', filamentSubtype: 'silk' })} />);
    expect(await screen.findByTestId('filament-match')).toHaveTextContent(/nothing loaded matches PLA silk.*waits in the queue/);
  });

  it('multi-material: renders three map-tool-* selects when modelFilaments has 3 items', async () => {
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    expect(await screen.findByTestId('map-tool-1')).not.toBeNull();
    expect(await screen.findByTestId('map-tool-2')).not.toBeNull();
    expect(await screen.findByTestId('map-tool-3')).not.toBeNull();
    expect(screen.queryByTestId('tool-select')).toBeNull();
  });

  it('multi-material: pinning filament 2 to a tool writes {model_filament, tool_index} with an undefined ask', async () => {
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    fireEvent.change(await screen.findByTestId('map-tool-2'), { target: { value: 't:2' } });
    expect(onChange).toHaveBeenCalledWith({
      filamentMap: expect.arrayContaining([
        { model_filament: 2, tool_index: 2, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null },
      ]),
    });
  });

  it('multi-material: two filaments may be pinned to the same tool (no error, no restriction)', async () => {
    const pinned = (i: number, t: number) => ({ model_filament: i, tool_index: t, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null });
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), filamentMap: [pinned(1, 0), pinned(2, 1), pinned(3, 2)] }, MODEL_FILAMENTS_3);
    fireEvent.change(await screen.findByTestId('map-tool-2'), { target: { value: 't:0' } });
    const map = onChange.mock.calls[0][0].filamentMap;
    expect(map.map((e: any) => [e.model_filament, e.tool_index])).toEqual([[1, 0], [2, 0], [3, 2]]);
  });

  it('multi-material: "Match by filament" gives the colour its own tuple editor and a match chip', async () => {
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    fireEvent.change(await screen.findByTestId('map-tool-2'), { target: { value: 'ask' } });
    expect(onChange).toHaveBeenCalledWith({
      filamentMap: expect.arrayContaining([
        { model_filament: 2, tool_index: null, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null },
      ]),
    });
  });

  it('multi-material: editing the tuple of an asked colour keeps the other colours and previews the matching slot', async () => {
    const asked = { model_filament: 2, tool_index: null, filament_manufacturer: null, filament_type: 'PETG', filament_subtype: 'any', filament_color: null };
    const pinned1 = { model_filament: 1, tool_index: 0, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null };
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), filamentMap: [pinned1, asked] }, MODEL_FILAMENTS_3.slice(0, 2));
    expect(await screen.findByTestId('map-match-2')).toHaveTextContent('✓ T1');
    fireEvent.change(screen.getByTestId('map-ask-2-subtype'), { target: { value: 'silk' } });
    const map = onChange.mock.calls[0][0].filamentMap;
    expect(map).toEqual([pinned1, { ...asked, filament_subtype: 'silk' }]);
  });

  it('multi-material: a single model filament still shows the tool control', async () => {
    renderCfg(MULTI, defaultPerPrinterCfg(), [{ index: 1, color: '#ff0000', type: 'PLA' }]);
    expect(await screen.findByTestId('tool-select')).not.toBeNull();
    expect(screen.queryByTestId('map-tool-1')).toBeNull();
  });

  it('multi-material: no modelFilaments prop still shows the tool control', async () => {
    renderCfg(MULTI);
    expect(await screen.findByTestId('tool-select')).not.toBeNull();
  });
});

const MATERIALS = [
  mkMaterial({ ref: '7', name: 'Sky Blue', vendor: 'ELEGOO', material: 'PLA', subtype: 'silk', color_hex: '#5B9BD5' }),
  mkMaterial({ ref: 'm-19', name: 'White', vendor: 'Sunlu', material: 'PETG', color_hex: '#FFFFFF' }),
];

describe('PerPrinterConfig — an inventory only fills the tuple', () => {
  beforeEach(() => {
    vi.mocked(inventoryApi.useInventory).mockReturnValue(activeInventory('local_inventory'));
    vi.mocked(inventoryApi.useMaterials).mockReturnValue(MATERIALS);
  });

  it('picking a material copies manufacturer, type, subtype and colour — whatever the provider, no ref is kept', async () => {
    const onChange = renderCfg(SINGLE);
    fireEvent.click(await screen.findByTestId('filament-ask-pick'));
    fireEvent.change(screen.getByTestId('filament-ask-material'), { target: { value: 'm-19' } });
    const patch = onChange.mock.calls[0][0];
    expect(patch).toEqual({ filamentManufacturer: 'Sunlu', filamentType: 'PETG', filamentSubtype: 'standard', filamentColor: '#FFFFFF' });
    expect(JSON.stringify(patch)).not.toMatch(/material|ref|provider|filament_?id/i);
  });

  it('a multi-material colour is filled from any provider\'s material (a non-numeric ref works)', async () => {
    const asked = { model_filament: 2, tool_index: null, filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null };
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), filamentMap: [asked] }, MODEL_FILAMENTS_3.slice(0, 2));
    fireEvent.click(await screen.findByTestId('map-ask-2-pick'));
    fireEvent.change(screen.getByTestId('map-ask-2-material'), { target: { value: 'm-19' } });
    expect(onChange).toHaveBeenCalledWith({ filamentMap: expect.arrayContaining([
      { model_filament: 2, tool_index: null, filament_manufacturer: 'Sunlu', filament_type: 'PETG', filament_subtype: 'standard', filament_color: '#FFFFFF' },
    ]) });
  });

  it('typing the tuple by hand needs no inventory at all', async () => {
    vi.mocked(inventoryApi.useInventory).mockReturnValue(noInventory());
    vi.mocked(inventoryApi.useMaterials).mockReturnValue([]);
    const onChange = renderCfg(SINGLE);
    expect(screen.queryByTestId('filament-ask-pick')).toBeNull();
    fireEvent.change(await screen.findByTestId('filament-ask-type'), { target: { value: 'ABS' } });
    expect(onChange).toHaveBeenCalledWith({ ...NO_ASK_CFG, filamentType: 'ABS', filamentSubtype: 'any' });
  });
});
