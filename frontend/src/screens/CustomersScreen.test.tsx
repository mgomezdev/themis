import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { CustomersScreen } from './CustomersScreen';

// Shapes mirror backend/app/api/routes/customers.py (list_customers, unlinked_projects).
const cust = (id: number, name: string, o: object = {}) => ({
  id, name, email: `${name.toLowerCase()}@x.test`, enabled: true, created_at: '2026-09-01T00:00:00',
  phone: null, company: null, notes: null, has_password: true,
  project_count: 0, active_project_count: 0, outstanding: 0, last_project_at: null, ...o,
});
const UNLINKED = [
  { project_id: 5, project_name: 'Brackets', customer_text: 'acme', created_at: '', suggested_customer_id: 1 },
  { project_id: 6, project_name: 'Mystery', customer_text: 'Stranger', created_at: '', suggested_customer_id: null },
];

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/customers/link-projects') return new Response('{"linked":1}', { status: 200 });
    if (url === '/api/v1/customers/unlinked-projects') return new Response(JSON.stringify(UNLINKED), { status: 200 });
    if (url === '/api/v1/customers' && !init?.method)
      return new Response(JSON.stringify([cust(1, 'Acme'), cust(2, 'Bob', { has_password: false })]), { status: 200 });
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
});

const renderScreen = () => render(<MemoryRouter initialEntries={['/customers']}><CustomersScreen /></MemoryRouter>);

describe('CustomersScreen', () => {
  it('shows whether each customer can sign in', async () => {
    renderScreen();
    const bobRow = (await screen.findByText('Bob')).closest('tr')!;
    expect(within(bobRow).getByText('No sign-in yet')).toBeTruthy();
    expect(within(screen.getByText('Acme').closest('tr')!).getByText('Portal enabled')).toBeTruthy();
  });

  it('links unlinked projects, pre-selecting exact matches', async () => {
    renderScreen();
    expect(await screen.findByText(/2 projects name a customer/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect((screen.getByRole('combobox', { name: 'Customer for Brackets' }) as HTMLSelectElement).value).toBe('1');
    expect((screen.getByRole('combobox', { name: 'Customer for Mystery' }) as HTMLSelectElement).value).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'Link 1 project' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/api/v1/customers/link-projects')).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([u]) => u === '/api/v1/customers/link-projects')!;
    expect(JSON.parse(init.body)).toEqual({ links: [{ project_id: 5, customer_id: 1 }] });
  });
});
