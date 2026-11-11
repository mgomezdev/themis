import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectItemsTable } from './ProjectItemsTable';
import type { LocalItem } from '../../lib/projectDraft';
import type { ProjectItem } from '../../api/projects';

const item = (localId: string, file_id: number, extra: Partial<LocalItem> = {}): LocalItem => ({
  localId, file_id, file_name: `${file_id}.stl`, quantity: 1, sort_order: 0,
  filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null, ...extra,
});
const progress = (id: number, quantity_completed: number, quantity_failed: number) =>
  [id, { id, quantity_completed, quantity_failed }] as [number, ProjectItem];

function setup(items: LocalItem[], serverItems = new Map<number, ProjectItem>()) {
  const dispatch = vi.fn();
  render(<ProjectItemsTable items={items} serverItems={serverItems} materials={[]} catalogEnabled={false} dispatch={dispatch} />);
  return dispatch;
}

describe('ProjectItemsTable', () => {
  it('prompts to add parts when there are none', () => {
    setup([]);
    expect(screen.getByText('Parts (0)')).toBeInTheDocument();
    expect(screen.getByText('Click an STL file on the left to add parts')).toBeInTheDocument();
    expect(screen.queryByText('Qty')).toBeNull();
  });

  it('shows one row per item with its quantity', () => {
    setup([item('a', 1, { quantity: 3 }), item('b', 2)]);
    expect(screen.getByText('Parts (2)')).toBeInTheDocument();
    expect(screen.getByTitle('1.stl')).toBeInTheDocument();
    expect(screen.getAllByRole('spinbutton').map(i => (i as HTMLInputElement).value)).toEqual(['3', '1']);
  });

  it('shows print progress for saved items that have any, flagging failures', () => {
    setup(
      [item('a', 1, { serverId: 50 }), item('b', 2, { serverId: 51 }), item('c', 3, { serverId: 52 }), item('d', 4)],
      new Map([progress(50, 2, 0), progress(51, 1, 2), progress(52, 0, 0)]),
    );
    const ok = screen.getByText('2/2 ok');
    expect(ok.style.color).toBe('var(--text-3)');
    const failed = screen.getByText(/1\/3 ok/);
    expect(failed.textContent).toBe('1/3 ok · 2 failed');
    expect(failed.style.color).toBe('var(--err)');
    expect(screen.getAllByText(/ ok/)).toHaveLength(2);
  });

  it('offers apply-to-all only with more than one item', () => {
    setup([item('a', 1)]);
    expect(screen.queryByTitle('Apply this filament to every part')).toBeNull();
  });

  it('dispatches quantity (clamped to 1..), filament, apply-to-all and remove for the right row', async () => {
    const dispatch = setup([item('a', 1), item('b', 2, { filament_type: 'PETG' })]);
    fireEvent.change(screen.getAllByRole('spinbutton')[1], { target: { value: '0' } });
    fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
    await userEvent.selectOptions(screen.getAllByLabelText('Filament type')[0], 'PETG');
    await userEvent.click(screen.getAllByTitle('Apply this filament to every part')[1]);
    await userEvent.click(screen.getAllByTitle('Remove')[0]);
    const calls = dispatch.mock.calls.map(c => c[0]);
    expect(calls[0]).toEqual({ type: 'updateItem', localId: 'b', patch: { quantity: 1 } });
    expect(calls[1]).toEqual({ type: 'updateItem', localId: 'a', patch: { quantity: 5 } });
    expect(calls[2]).toMatchObject({ type: 'updateItem', localId: 'a', patch: { filament_type: 'PETG' } });
    expect(Object.keys(calls[2].patch).sort()).toEqual(['filament_color', 'filament_manufacturer', 'filament_subtype', 'filament_type']);
    expect(calls[3]).toEqual({
      type: 'applyFilamentToAll',
      ask: { filament_manufacturer: null, filament_type: 'PETG', filament_subtype: 'any', filament_color: null },
    });
    expect(calls[4]).toEqual({ type: 'remove', kind: 'item', localId: 'a' });
  });
});
