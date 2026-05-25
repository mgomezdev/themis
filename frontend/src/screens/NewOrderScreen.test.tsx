import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewOrderScreen } from './NewOrderScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('NewOrderScreen', () => {
  it('renders order type selector', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.getByText('Customer order')).toBeTruthy();
    expect(screen.getByText('Internal project')).toBeTruthy();
  });
  it('renders parts table', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.getByText('Arm bracket — L')).toBeTruthy();
  });
  it('add row button works', async () => {
    const user = userEvent.setup();
    render(<NewOrderScreen />, { wrapper });
    const rowsBefore = screen.getAllByRole('row').length;
    await user.click(screen.getAllByRole('button', { name: /add part|add row/i })[0]);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(rowsBefore);
  });
});
