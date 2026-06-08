import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PerPrinterConfig, defaultPerPrinterCfg } from './PerPrinterConfig';

// Mock spoolman hooks so they don't fire real HTTP requests
vi.mock('../api/spoolman', () => ({
  useSpoolmanConfig: vi.fn().mockReturnValue({ config: null, refetch: vi.fn() }),
  useFilaments: vi.fn().mockReturnValue([]),
  filamentDisplayName: vi.fn((f: { vendor?: { name: string }; name: string }) =>
    f.vendor ? `${f.vendor.name} ${f.name}` : f.name),
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

function renderCfg(printer: any, config = defaultPerPrinterCfg()) {
  const onChange = vi.fn();
  render(<PerPrinterConfig printerId={String(printer.id)} printers={[printer as any]} config={config} onChange={onChange} />);
  return onChange;
}

describe('PerPrinterConfig', () => {
  it('defaultPerPrinterCfg is all-null (defer)', () => {
    expect(defaultPerPrinterCfg()).toEqual({
      printProfile: null, filamentProfile: null, filamentId: null,
      filamentType: null, filamentColor: null, toolIndex: null,
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
});
