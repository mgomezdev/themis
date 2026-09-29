import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { CustomerDetailScreen } from './CustomerDetailScreen';

// Shape mirrors backend/app/api/routes/customers.py get_customer().
const win = (o: Partial<Record<string, number>>) => ({
  project_count: 0, revenue: 0, expenses: 0, profit: 0, billed: 0, outstanding: 0, ...o,
});
const project = (o: object) => ({
  stage: 'queued', on_hold: false, due_date: null, created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z', jobs_total: 0, jobs_complete: 0, status: 'pending',
  price: null, amount_paid: null, payment_status: 'unpaid', filament_cost_total: null, outstanding: 0, ...o,
});
const CUSTOMER = {
  id: 7, name: 'Acme Corp', email: 'ops@acme.test', enabled: true, created_at: '2026-01-01T00:00:00',
  phone: '555-1234', company: 'Acme Inc', notes: null, has_password: false,
  projects: [
    project({ id: 1, name: 'Brackets', status: 'active', jobs_total: 2, jobs_complete: 1,
              price: 50, amount_paid: 20, payment_status: 'partial', outstanding: 30 }),
    project({ id: 2, name: 'Old Order', status: 'completed', jobs_total: 1, jobs_complete: 1,
              price: 100, amount_paid: 100, payment_status: 'paid' }),
  ],
  financials: {
    windows: {
      '30d': win({ project_count: 1, revenue: 20, expenses: 3, profit: 17, billed: 50, outstanding: 30 }),
      '60d': win({ project_count: 1, revenue: 20, expenses: 3, profit: 17, billed: 50, outstanding: 30 }),
      '90d': win({ project_count: 2, revenue: 120, expenses: 15.5, profit: 104.5, billed: 150, outstanding: 30 }),
      all:   win({ project_count: 2, revenue: 120, expenses: 15.5, profit: 104.5, billed: 150, outstanding: 30 }),
    },
    unpriced_unpaid: 1,
  },
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={['/customers/7']}>
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailScreen />} />
        <Route path="/projects/:id" element={<div>project page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.restoreAllMocks();
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return new Response('{"deleted":7,"projects_unlinked":2}', { status: 200 });
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ ...CUSTOMER, ...JSON.parse(init.body as string) }), { status: 200 });
    if (url.endsWith('/api/v1/customers/7')) return new Response(JSON.stringify(CUSTOMER), { status: 200 });
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
});

describe('CustomerDetailScreen', () => {
  it('shows the financial summary for every period', async () => {
    renderScreen();
    const table = await screen.findByTestId('financial-table');
    const headers = within(table).getAllByRole('columnheader').map(h => h.textContent);
    expect(headers).toEqual(['', '30 days', '60 days', '90 days', 'All time']);
    const revenueRow = within(table).getByText('Revenue').closest('tr')!;
    expect(within(revenueRow).getAllByRole('cell').slice(1).map(c => c.textContent))
      .toEqual(['$20.00', '$20.00', '$120.00', '$120.00']);
    expect(screen.getByText(/1 unpaid project has no price set/)).toBeTruthy();
  });

  it('splits projects into current and past', async () => {
    renderScreen();
    await screen.findByText('Brackets');
    expect(screen.queryByText('Old Order')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /past/i }));
    expect(screen.getByText('Old Order')).toBeTruthy();
    expect(screen.queryByText('Brackets')).toBeNull();
    await userEvent.click(screen.getByText('Old Order'));
    expect(await screen.findByText('project page')).toBeTruthy();
  });

  it('flags a customer who has no password yet', async () => {
    renderScreen();
    expect(await screen.findByText('No sign-in yet')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Set password' })).toBeTruthy();
  });

  it('deletes after confirmation and returns to the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(
      <MemoryRouter initialEntries={['/customers/7']}>
        <Routes>
          <Route path="/customers/:id" element={<CustomerDetailScreen />} />
          <Route path="/customers" element={<div>customer list</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /delete/i }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('2 projects are kept but unlinked'));
    expect(await screen.findByText('customer list')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([u, i]) => u === '/api/v1/customers/7' && i?.method === 'DELETE')).toBe(true);
  });

  it('saves edited details with PATCH', async () => {
    renderScreen();
    const phone = await screen.findByDisplayValue('555-1234');
    const save = screen.getByRole('button', { name: /save changes/i }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await userEvent.clear(phone);
    await userEvent.type(phone, '555-9999');
    await userEvent.click(save);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => i?.method === 'PATCH')).toBe(true));
    const [url, init] = fetchMock.mock.calls.find(([, i]) => i?.method === 'PATCH')!;
    expect(url).toBe('/api/v1/customers/7');
    expect(JSON.parse(init.body)).toEqual({
      name: 'Acme Corp', email: 'ops@acme.test', phone: '555-9999', company: 'Acme Inc', notes: '',
    });
  });
});
