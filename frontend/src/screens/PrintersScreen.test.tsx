import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen } from './PrintersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('PrintersScreen', () => {
  it('renders printer table', () => {
    render(<PrintersScreen />, { wrapper });
    expect(screen.getByText('Atlas')).toBeTruthy();
    expect(screen.getByText('Forge')).toBeTruthy();
  });
  it('shows Add printer button', () => {
    render(<PrintersScreen />, { wrapper });
    expect(screen.getByRole('button', { name: /add printer/i })).toBeTruthy();
  });
  it('clicking Add printer shows wizard step 1', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    expect(screen.getByText('Pick a printer model')).toBeTruthy();
  });
  it('wizard advances to step 2', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText(/How should we talk/i)).toBeTruthy();
  });
});
