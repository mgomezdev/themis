import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MachinePicker } from './MachinePicker';
import type { MachinePreset } from '../api/printers';

const CATALOG: MachinePreset[] = [
  { name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' },
  { name: 'Bambu Lab P1S 0.6 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.6', source: 'system' },
];

describe('MachinePicker', () => {
  it('resolves make→model→nozzle to a preset name via onChange', () => {
    const onChange = vi.fn();
    render(<MachinePicker catalog={CATALOG} value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Make'), { target: { value: 'Bambu Lab' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'P1S' } });
    fireEvent.change(screen.getByLabelText('Nozzle'), { target: { value: '0.4' } });
    expect(onChange).toHaveBeenLastCalledWith('Bambu Lab P1S 0.4 nozzle');
  });
});
