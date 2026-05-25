import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';

describe('Sidebar', () => {
  it('renders nav items', () => {
    render(
      <MemoryRouter initialEntries={['/queue']}>
        <Sidebar queueCount={3} ordersOpen={2} />
      </MemoryRouter>
    );
    expect(screen.getByText('Job queue')).toBeTruthy();
    expect(screen.getByText('Fleet')).toBeTruthy();
    expect(screen.getByText('Orders')).toBeTruthy();
  });
  it('shows queue count badge', () => {
    render(
      <MemoryRouter initialEntries={['/queue']}>
        <Sidebar queueCount={5} ordersOpen={0} />
      </MemoryRouter>
    );
    expect(screen.getByText('5')).toBeTruthy();
  });
});
