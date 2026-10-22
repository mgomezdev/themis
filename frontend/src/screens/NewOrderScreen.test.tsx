import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewOrderScreen } from './NewOrderScreen';
import * as ordersApi from '../api/orders';

vi.mock('../api/orders', async importOriginal => ({
  ...(await importOriginal<typeof import('../api/orders')>()),
  createOrder: vi.fn().mockResolvedValue({ id: 5 }),
}));

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;
beforeEach(() => vi.clearAllMocks());

describe('NewOrderScreen', () => {
  it('offers no customer order type and points customer work at projects', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.queryByText('Customer order')).toBeNull();
    expect(screen.getByTestId('orders-are-internal').textContent).toMatch(/record a project/);
  });

  it('starts with an empty part row and can add rows', async () => {
    const user = userEvent.setup();
    render(<NewOrderScreen />, { wrapper });
    const rowsBefore = screen.getAllByRole('row').length;
    await user.click(screen.getAllByRole('button', { name: /add part|add row/i })[0]);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(rowsBefore);
  });

  it('has no suggested plates panel', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.queryByText(/suggested plates/i)).toBeNull();
  });

  it('sends each part\'s filament as a tuple (type, subtype, maker, colour) — typed by hand, no inventory needed', async () => {
    const user = userEvent.setup();
    render(<NewOrderScreen />, { wrapper });
    await user.type(screen.getByPlaceholderText('Part name'), 'Bracket');
    await user.type(screen.getByPlaceholderText(/e\.g\. Mk3 chassis/), 'Batch 5');          // the order needs a customer and a title
    await user.type(screen.getByPlaceholderText(/R&D/), 'Internal shop');
    await user.type(screen.getByLabelText('Manufacturer'), 'Acme');
    await user.selectOptions(screen.getByLabelText('Filament type'), 'PETG');
    await user.selectOptions(screen.getByLabelText('Filament subtype'), 'silk');
    await user.click(screen.getByRole('button', { name: /create order|save/i }));

    await vi.waitFor(() => expect(ordersApi.createOrder).toHaveBeenCalled());
    const part = vi.mocked(ordersApi.createOrder).mock.calls[0][0].parts[0];
    expect(part).toMatchObject({ name: 'Bracket', material: 'PETG', filament_manufacturer: 'Acme', filament_type: 'PETG', filament_subtype: 'silk', filament_color: null });
    expect(Object.keys(part).filter(k => /material_|filament_id/.test(k))).toEqual([]);
  });
});
