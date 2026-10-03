import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewOrderScreen } from './NewOrderScreen';

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
});
