import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FilamentAskEditor } from './FilamentAskEditor';
import { mkMaterial } from '../test/inventoryFixtures';
import { NO_ASK, type FilamentAsk } from '../lib/filamentAsk';

const ask = (over: Partial<FilamentAsk> = {}): FilamentAsk => ({ ...NO_ASK, ...over });

describe('FilamentAskEditor', () => {
  it('choosing a type gives it the subtype "any"; clearing the type clears the subtype', () => {
    const onChange = vi.fn();
    const { rerender } = render(<FilamentAskEditor value={NO_ASK} onChange={onChange} />);
    expect(screen.getByTestId('ask-subtype')).toBeDisabled();
    fireEvent.change(screen.getByTestId('ask-type'), { target: { value: 'PLA' } });
    expect(onChange).toHaveBeenLastCalledWith(ask({ filament_type: 'PLA', filament_subtype: 'any' }));

    rerender(<FilamentAskEditor value={ask({ filament_type: 'PLA', filament_subtype: 'silk' })} onChange={onChange} />);
    fireEvent.change(screen.getByTestId('ask-type'), { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith(ask());
  });

  it('keeps the subtype wildcard and plain filament as distinct choices', () => {
    const onChange = vi.fn();
    render(<FilamentAskEditor value={ask({ filament_type: 'PETG', filament_subtype: 'any' })} onChange={onChange} />);
    const sub = screen.getByTestId('ask-subtype') as HTMLSelectElement;
    expect([...sub.options].map(o => o.value)).toEqual(expect.arrayContaining(['any', 'standard', 'silk']));
    fireEvent.change(sub, { target: { value: 'standard' } });
    expect(onChange).toHaveBeenLastCalledWith(ask({ filament_type: 'PETG', filament_subtype: 'standard' }));
  });

  it('types a manufacturer by hand and blank means any maker', () => {
    const onChange = vi.fn();
    render(<FilamentAskEditor value={NO_ASK} onChange={onChange} />);
    fireEvent.change(screen.getByTestId('ask-manufacturer'), { target: { value: 'Acme' } });
    expect(onChange).toHaveBeenLastCalledWith(ask({ filament_manufacturer: 'Acme' }));
    fireEvent.change(screen.getByTestId('ask-manufacturer'), { target: { value: '  ' } });
    expect(onChange).toHaveBeenLastCalledWith(NO_ASK);
  });

  it('sets and clears a specific color', () => {
    const onChange = vi.fn();
    const { rerender } = render(<FilamentAskEditor value={NO_ASK} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('ask-color'));
    expect(onChange).toHaveBeenLastCalledWith(ask({ filament_color: '#1a73e8' }));
    rerender(<FilamentAskEditor value={ask({ filament_color: '#ff0000' })} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('Any color'));
    expect(onChange).toHaveBeenLastCalledWith(NO_ASK);
  });

  it('fills all four parts from an inventory material and keeps no reference to it', () => {
    const onChange = vi.fn();
    const materials = [mkMaterial({ ref: '5', name: 'Silk Red', material: 'PLA', subtype: 'silk', vendor: 'Acme', color_hex: '#FF0000' })];
    render(<FilamentAskEditor value={NO_ASK} onChange={onChange} materials={materials} catalogEnabled />);
    fireEvent.click(screen.getByTestId('ask-pick'));
    fireEvent.change(screen.getByTestId('ask-material'), { target: { value: '5' } });
    expect(onChange).toHaveBeenCalledWith(ask({ filament_manufacturer: 'Acme', filament_type: 'PLA', filament_subtype: 'silk', filament_color: '#FF0000' }));
  });

  it('offers no catalog without an inventory (the tuple is still fully editable)', () => {
    render(<FilamentAskEditor value={NO_ASK} onChange={() => {}} materials={[]} catalogEnabled={false} />);
    expect(screen.queryByTestId('ask-pick')).toBeNull();
    expect(screen.getByTestId('ask-type')).toBeEnabled();
  });
});

describe('FilamentAskEditor typing', () => {
  it('keeps the space while a multi-word manufacturer is typed ("Bambu Lab")', () => {
    const onChange = vi.fn();
    const { rerender } = render(<FilamentAskEditor value={NO_ASK} onChange={onChange} />);
    for (const text of ['Bambu', 'Bambu ', 'Bambu Lab']) {
      fireEvent.change(screen.getByTestId('ask-manufacturer'), { target: { value: text } });
      expect(onChange).toHaveBeenLastCalledWith({ ...NO_ASK, filament_manufacturer: text });
      rerender(<FilamentAskEditor value={{ ...NO_ASK, filament_manufacturer: text }} onChange={onChange} />);
      expect(screen.getByTestId('ask-manufacturer')).toHaveValue(text);
    }
  });
});
