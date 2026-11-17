import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectPartsEditor } from './ProjectPartsEditor';

const PARTS = [
  { localId: 'a', name: 'magnet', quantity: 4, allocated: true, unitCost: '0.25' },
  { localId: 'b', name: '', quantity: 1, allocated: false, unitCost: '' },
];
const qty = () => screen.getAllByRole('spinbutton').filter(i => i.getAttribute('aria-label') !== 'Unit cost');

describe('ProjectPartsEditor', () => {
  it('lists the parts with a count', () => {
    render(<ProjectPartsEditor parts={PARTS} dispatch={() => {}} />);
    expect(screen.getByText('Non-printed parts (2)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('magnet')).toBeInTheDocument();
    expect(qty().map(i => (i as HTMLInputElement).value)).toEqual(['4', '1']);
    expect(screen.getAllByLabelText('Unit cost').map(i => (i as HTMLInputElement).value)).toEqual(['0.25', '']);
    expect(screen.getAllByRole('checkbox', { name: 'Allocated' }).map(c => (c as HTMLInputElement).checked)).toEqual([true, false]);
  });

  it('dispatches add, edits and remove for the right row', async () => {
    const dispatch = vi.fn();
    render(<ProjectPartsEditor parts={PARTS} dispatch={dispatch} />);
    await userEvent.click(screen.getByRole('button', { name: '+ Add part' }));
    fireEvent.change(screen.getByDisplayValue('magnet'), { target: { value: 'screw' } });
    fireEvent.change(qty()[1], { target: { value: '3' } });
    fireEvent.change(screen.getAllByLabelText('Unit cost')[1], { target: { value: '2' } });
    await userEvent.click(screen.getAllByRole('checkbox', { name: 'Allocated' })[1]);
    await userEvent.click(screen.getAllByTitle('Remove part')[0]);
    expect(dispatch.mock.calls.map(c => c[0])).toEqual([
      { type: 'addPart' },
      { type: 'updatePart', localId: 'a', patch: { name: 'screw' } },
      { type: 'updatePart', localId: 'b', patch: { quantity: 3 } },
      { type: 'updatePart', localId: 'b', patch: { unitCost: '2' } },
      { type: 'updatePart', localId: 'b', patch: { allocated: true } },
      { type: 'remove', kind: 'part', localId: 'a' },
    ]);
  });

  it.each([['0', 1], ['-2', 1], ['', 1], ['7', 7]])('clamps a quantity of %j to %d', (value, expected) => {
    const dispatch = vi.fn();
    render(<ProjectPartsEditor parts={PARTS} dispatch={dispatch} />);
    fireEvent.change(qty()[0], { target: { value } });
    expect(dispatch).toHaveBeenLastCalledWith({ type: 'updatePart', localId: 'a', patch: { quantity: expected } });
  });
});
