import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ProcessPresetPicker } from './ProcessPresetPicker';
import * as queueApi from '../api/queue';

vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/queue')>();
  return { ...actual, getPrinterProfiles: vi.fn() };
});

beforeEach(() => vi.clearAllMocks());

describe('ProcessPresetPicker', () => {
  it('shows a hint instead of a select when no printers are selected', () => {
    render(<ProcessPresetPicker printerIds={[]} value={null} onChange={vi.fn()} />);
    expect(screen.queryByTestId('process-preset-select')).toBeNull();
    expect(screen.getByText(/select printers/i)).toBeTruthy();
  });

  it('offers only presets common to every selected printer', async () => {
    vi.mocked(queueApi.getPrinterProfiles).mockImplementation(async (id: number) => ({
      print_profiles: id === 1 ? ['0.20mm Standard', '0.16mm Fine'] : ['0.20mm Standard', '0.28mm Draft'],
      filament_profiles: [],
    }));

    render(<ProcessPresetPicker printerIds={[1, 2]} value={null} onChange={vi.fn()} />);

    const sel = await screen.findByTestId('process-preset-select');
    await waitFor(() => {
      const options = Array.from((sel as HTMLSelectElement).options).map(o => o.value);
      expect(options).toContain('0.20mm Standard');
      expect(options).not.toContain('0.16mm Fine');
      expect(options).not.toContain('0.28mm Draft');
    });
  });

  it('calls onChange when a preset is picked', async () => {
    vi.mocked(queueApi.getPrinterProfiles).mockResolvedValue({
      print_profiles: ['0.20mm Standard'], filament_profiles: [],
    });
    const onChange = vi.fn();
    render(<ProcessPresetPicker printerIds={[1]} value={null} onChange={onChange} />);

    const sel = await screen.findByTestId('process-preset-select');
    await waitFor(() => expect((sel as HTMLSelectElement).options.length).toBeGreaterThan(1));
    fireEvent.change(sel, { target: { value: '0.20mm Standard' } });
    expect(onChange).toHaveBeenCalledWith('0.20mm Standard');
  });

  it('clears a selection that is no longer valid for the current printer set', async () => {
    vi.mocked(queueApi.getPrinterProfiles).mockResolvedValue({
      print_profiles: ['0.16mm Fine'], filament_profiles: [],
    });
    const onChange = vi.fn();
    render(<ProcessPresetPicker printerIds={[1]} value="0.20mm Standard" onChange={onChange} />);

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(null));
  });
});
