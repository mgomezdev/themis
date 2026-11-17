import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ItemSourcePicker, initialSource } from './ItemSourcePicker';
import { stubFetch } from '../test/fetchStub';

describe('ItemSourcePicker (BIZ-356)', () => {
  it('starts each file kind as the right item', () => {
    expect(initialSource('stl')).toEqual({ kind: 'stl', source_ref: null, pack_rule: 'split' });
    expect(initialSource('3mf')).toEqual({ kind: '3mf_plate', source_ref: '1', pack_rule: 'split' });
    expect(initialSource('gcode_3mf')).toEqual({ kind: 'gcode', source_ref: '1', pack_rule: 'locked' });
  });

  it('offers a 3MF’s plates and objects, and switching to an object keeps only the rules an object can use', async () => {
    stubFetch({ 'GET /api/v1/files/4/objects': [{ id: '8', name: 'Gear', instances: 2 }] });
    const onChange = vi.fn();
    render(<ItemSourcePicker fileId={4} fileKind="3mf" plateCount={2} kind="3mf_plate" sourceRef="1" packRule="together"
                             onChange={onChange} />);
    const source = screen.getByLabelText('Plate or object') as HTMLSelectElement;
    await waitFor(() => expect([...source.options].map(o => o.textContent)).toEqual(
      ['Plate 1 (all its objects)', 'Plate 2 (all its objects)', 'Object: Gear']));
    expect([...(screen.getByLabelText('Packing rule') as HTMLSelectElement).options].map(o => o.value))
      .toEqual(['split', 'together', 'solo', 'locked']);
    fireEvent.change(source, { target: { value: 'o:8' } });
    expect(onChange).toHaveBeenCalledWith({ kind: '3mf_object', source_ref: '8', pack_rule: 'split' });   // together → split
  });

  it('an STL only splits or goes solo; gcode prints as sliced; a saved item keeps its source', () => {
    const { rerender } = render(<ItemSourcePicker fileId={1} fileKind="stl" plateCount={1} kind="stl" sourceRef={null}
                                                  packRule="split" onChange={() => {}} />);
    expect([...(screen.getByLabelText('Packing rule') as HTMLSelectElement).options].map(o => o.value)).toEqual(['split', 'solo']);
    expect(screen.queryByLabelText('Plate or object')).toBeNull();
    rerender(<ItemSourcePicker fileId={2} fileKind="gcode" plateCount={1} kind="gcode" sourceRef="1" packRule="locked"
                               onChange={() => {}} />);
    expect(screen.getByText('As sliced')).toBeTruthy();
    rerender(<ItemSourcePicker fileId={3} fileKind="3mf" plateCount={2} kind="3mf_plate" sourceRef="2" packRule="split"
                               fixedSource onChange={() => {}} />);
    expect((screen.getByLabelText('Plate or object') as HTMLSelectElement).disabled).toBe(true);
  });
});
