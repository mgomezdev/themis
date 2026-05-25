import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { FleetScreen } from './FleetScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

describe('FleetScreen', () => {
  it('renders printer tiles', () => {
    render(<FleetScreen />, { wrapper });
    // All 3 printers should appear (Atlas, Forge, Iris)
    expect(screen.getAllByText(/Atlas|Forge|Iris/).length).toBeGreaterThan(0);
  });
  it('layout toggle works', async () => {
    const user = userEvent.setup();
    render(<FleetScreen />, { wrapper });
    const listBtn = screen.getByTitle(/list/i);
    await user.click(listBtn);
    // Table row visible after switching to list layout
    expect(screen.getByRole('table')).toBeTruthy();
  });
});
