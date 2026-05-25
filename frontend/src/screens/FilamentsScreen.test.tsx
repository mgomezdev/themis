import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FilamentsScreen } from './FilamentsScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FilamentsScreen', () => {
  it('renders summary stats', () => {
    render(<FilamentsScreen />, { wrapper });
    expect(screen.getByText('Spools tracked')).toBeTruthy();
  });
  it('renders filament rows', () => {
    render(<FilamentsScreen />, { wrapper });
    expect(screen.getByText('PolyTerra Charcoal')).toBeTruthy();
  });
  it('type filter works', async () => {
    const user = userEvent.setup();
    render(<FilamentsScreen />, { wrapper });
    const petgBtn = screen.getByRole('button', { name: /PETG/i });
    await user.click(petgBtn);
    expect(screen.queryByText('NinjaFlex Signal Red')).toBeNull();
  });
  it('clicking a row opens detail panel', async () => {
    const user = userEvent.setup();
    render(<FilamentsScreen />, { wrapper });
    const firstRow = screen.getByText('PolyTerra Charcoal').closest('.card');
    if (firstRow) await user.click(firstRow as HTMLElement);
    expect(screen.getByText('Print profiles')).toBeTruthy();
  });
});
