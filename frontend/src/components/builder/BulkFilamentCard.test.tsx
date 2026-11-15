import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { BulkFilamentCard } from './BulkFilamentCard';
import { NO_ASK } from '../../lib/filamentAsk';

const BULK = { ids: ['a', 'b'], value: { ...NO_ASK, filament_type: 'PLA' } };

function setup() {
  const props = { onChange: vi.fn(), onCancel: vi.fn(), onApply: vi.fn() };
  render(<BulkFilamentCard bulkAsk={BULK} materials={[]} catalogEnabled={false} {...props} />);
  return props;
}

describe('BulkFilamentCard', () => {
  it('says how many rows it sets and shows the current ask', () => {
    setup();
    expect(screen.getByText('Filament for 2 selected')).toBeInTheDocument();
    expect(screen.getByLabelText('Filament type')).toHaveValue('PLA');
  });

  it('reports an edit with the same rows', async () => {
    const p = setup();
    await userEvent.selectOptions(screen.getByLabelText('Filament type'), 'PETG');
    expect(p.onChange).toHaveBeenLastCalledWith({ ids: ['a', 'b'], value: expect.objectContaining({ filament_type: 'PETG' }) });
  });

  it('cancels or applies', async () => {
    const p = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(p.onCancel).toHaveBeenCalledTimes(1);
    expect(p.onApply).toHaveBeenCalledTimes(1);
  });
});
