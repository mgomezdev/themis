import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ProjectsScreen } from './ProjectsScreen';

const project = (id: number, name: string, o: object) => ({
  id, name, customer: '', order_type: 'customer', on_hold: false, due_date: null, notes: null,
  result_file_id: null, source_app: null, source_user: null, source_layout_id: null,
  amount_paid: null, price: null, payment_status: 'unpaid', stage: 'queued', customer_id: null, customer_name: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', items: [], links: [], parts: [],
  jobs_total: 0, jobs_complete: 0, estimate_filament_grams_total: null, estimate_seconds_total: null,
  estimate_filament_grams_remaining: null, estimate_seconds_remaining: null, actual_filament_grams: null,
  actual_seconds: null, filament_cost_total: null, ...o,
});
const PROJECTS = [
  project(1, 'Acme Brackets', { customer_id: 7, customer_name: 'Acme Corp', customer: 'acme' }),
  project(2, 'Walk-in Order', { customer: 'Walk-in Wendy' }),
  project(3, 'Shop Jig', { order_type: 'internal' }),
];

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    new Response(url === '/api/v1/projects' ? JSON.stringify(PROJECTS) : '[]', { status: 200 })));
  class FakeWS { onmessage = null; onopen = null; onclose = null; close() {} send() {} }
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);
});

describe('ProjectsScreen customers', () => {
  it('shows the linked account (as a link) or the typed name on each card', async () => {
    render(<MemoryRouter><ProjectsScreen /></MemoryRouter>);
    expect((await screen.findByRole('link', { name: 'Acme Corp' })).getAttribute('href')).toBe('/customers/7');
    expect(screen.getByText('Walk-in Wendy', { selector: 'div' })).toBeTruthy();
  });

  it('filters by customer', async () => {
    render(<MemoryRouter><ProjectsScreen /></MemoryRouter>);
    await screen.findByText('Acme Brackets');
    const select = screen.getByRole('combobox', { name: 'Filter by customer' });
    await userEvent.selectOptions(select, 'id:7');
    expect(screen.getByText('Acme Brackets')).toBeTruthy();
    expect(screen.queryByText('Walk-in Order')).toBeNull();
    expect(screen.queryByText('Shop Jig')).toBeNull();
    await userEvent.selectOptions(select, 'none');
    expect(screen.getByText('Shop Jig')).toBeTruthy();
    expect(screen.queryByText('Acme Brackets')).toBeNull();
  });
});
