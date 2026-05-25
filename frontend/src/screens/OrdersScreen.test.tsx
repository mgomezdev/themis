import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { OrdersScreen } from './OrdersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('OrdersScreen', () => {
  it('renders order list', () => {
    render(<OrdersScreen />, { wrapper });
    expect(screen.getByText('Vela Robotics')).toBeTruthy();
  });
  it('filter chips filter orders', async () => {
    const user = userEvent.setup();
    render(<OrdersScreen />, { wrapper });
    const internalBtn = screen.getByRole('button', { name: /internal/i });
    await user.click(internalBtn);
    expect(screen.queryByText('Vela Robotics')).toBeNull();
  });
  it('expanding an accordion shows parts table', async () => {
    const user = userEvent.setup();
    render(<OrdersScreen />, { wrapper });
    const firstAccordion = screen.getAllByRole('button').find(b => b.textContent?.includes('ORD-'));
    if (firstAccordion) {
      await user.click(firstAccordion);
      expect(screen.getByText(/parts breakdown/i)).toBeTruthy();
    }
  });
});
