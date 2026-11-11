import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ItemRow } from './ItemRow';
import type { LocalItem } from '../../lib/projectDraft';
import type { ProjectItem } from '../../api/projects';
import type { LibraryFile } from '../../data/types';
import { stubFetch } from '../../test/fetchStub';

const ITEM: LocalItem = {
  localId: 'a', file_id: 1, file_name: 'Kit.3mf', quantity: 2, sort_order: 0,
  kind: '3mf_plate', source_ref: '1', pack_rule: 'split', group_id: null,
  filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null,
};
const FILE = { id: 1, original_filename: 'Kit.3mf', kind: '3mf', plate_count: 3 } as LibraryFile;
const progress = (quantity_completed: number, quantity_failed: number) => ({ id: 50, quantity_completed, quantity_failed }) as ProjectItem;

afterEach(() => vi.unstubAllGlobals());

function setup(over: Partial<Parameters<typeof ItemRow>[0]> = {}) {
  stubFetch({ 'GET /api/v1/files/1/objects': [{ id: 'o7', name: 'Lid' }] });
  const dispatch = vi.fn();
  render(<ItemRow item={ITEM} file={FILE} serverItem={undefined} materials={[]} catalogEnabled={false}
                  canApplyToAll dispatch={dispatch} {...over} />);
  return dispatch;
}

describe('ItemRow', () => {
  it('shows the file, its quantity and filament', () => {
    setup();
    expect(screen.getByTitle('Kit.3mf')).toHaveTextContent('Kit.3mf');
    expect(screen.getByLabelText('Quantity of Kit.3mf')).toHaveValue(2);
    expect(screen.getByLabelText('Filament type')).toHaveValue('PLA');
  });

  it('dispatches a quantity change, clamped to at least 1', () => {
    const dispatch = setup();
    fireEvent.change(screen.getByLabelText('Quantity of Kit.3mf'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Quantity of Kit.3mf'), { target: { value: '0' } });
    expect(dispatch.mock.calls.map(c => c[0])).toEqual([
      { type: 'updateItem', localId: 'a', patch: { quantity: 5 } },
      { type: 'updateItem', localId: 'a', patch: { quantity: 1 } },
    ]);
  });

  it('dispatches a filament change as just the ask fields', async () => {
    const dispatch = setup();
    await userEvent.selectOptions(screen.getByLabelText('Filament type'), 'PETG');
    const call = dispatch.mock.calls[0][0];
    expect(call).toMatchObject({ type: 'updateItem', localId: 'a', patch: { filament_type: 'PETG' } });
    expect(Object.keys(call.patch).sort()).toEqual(['filament_color', 'filament_manufacturer', 'filament_subtype', 'filament_type']);
  });

  it('applies its filament to every item, and removes itself', async () => {
    const dispatch = setup();
    await userEvent.click(screen.getByTitle('Apply this filament to every part'));
    await userEvent.click(screen.getByTitle('Remove'));
    expect(dispatch.mock.calls.map(c => c[0])).toEqual([
      { type: 'applyFilamentToAll', ask: { filament_manufacturer: null, filament_type: 'PLA', filament_subtype: 'any', filament_color: null } },
      { type: 'removeItems', localIds: ['a'] },
    ]);
  });

  it('offers apply-to-all only when asked to', () => {
    setup({ canApplyToAll: false });
    expect(screen.queryByTitle('Apply this filament to every part')).toBeNull();
  });

  it('shows the file\'s plates in the source picker, and dispatches a source change', async () => {
    const dispatch = setup();
    const source = screen.getByLabelText('Plate or object') as HTMLSelectElement;
    expect(Array.from(source.options).map(o => o.value)).toEqual(['p:1', 'p:2', 'p:3']);   // plate_count from the file
    await screen.findByRole('option', { name: 'Object: Lid' });
    await userEvent.selectOptions(source, 'p:3');
    expect(dispatch).toHaveBeenLastCalledWith({ type: 'updateItem', localId: 'a', patch: { kind: '3mf_plate', source_ref: '3', pack_rule: 'split' } });
    await userEvent.selectOptions(screen.getByLabelText('Packing rule'), 'together');
    expect(dispatch).toHaveBeenLastCalledWith({ type: 'updateItem', localId: 'a', patch: { kind: '3mf_plate', source_ref: '1', pack_rule: 'together' } });
  });

  it('locks the source of a saved item, and infers the file kind from the item when the file is unknown', () => {
    setup({ item: { ...ITEM, serverId: 50 }, file: undefined });
    expect(screen.getByLabelText('Plate or object')).toBeDisabled();
    expect(Array.from((screen.getByLabelText('Plate or object') as HTMLSelectElement).options).map(o => o.value)).toEqual(['p:1']);
  });

  it.each([
    [undefined, null],
    [progress(0, 0), null],
    [progress(2, 0), '2/2 ok'],
    [progress(1, 2), '1/3 ok · 2 failed'],
  ])('shows print progress %j as %j', (serverItem, text) => {
    setup({ serverItem, item: { ...ITEM, serverId: 50 } });
    const el = screen.queryByText(/ ok/);
    expect(el?.textContent ?? null).toBe(text);
    if (el) expect(el.style.color).toBe(text!.includes('failed') ? 'var(--err)' : 'var(--text-3)');
  });
});
