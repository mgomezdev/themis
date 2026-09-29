import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { CustomerPicker, type CustomerChoice } from './CustomerPicker';

const cust = (id: number, name: string, company: string | null = null) => ({
  id, name, email: `${name.toLowerCase()}@x.test`, enabled: true, created_at: '2026-09-01T00:00:00',
  phone: null, company, notes: null, has_password: false,
  project_count: 0, active_project_count: 0, outstanding: 0, last_project_at: null,
});

let changes: CustomerChoice[];
function Harness({ initial }: { initial: CustomerChoice }) {
  const [v, setV] = useState(initial);
  return <CustomerPicker value={v} onChange={nv => { changes.push(nv); setV(nv); }} />;
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  changes = [];
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/customers' && init?.method === 'POST')
      return new Response(JSON.stringify({ ...cust(9, 'Zed'), ...JSON.parse(init.body as string) }), { status: 201 });
    if (url === '/api/v1/customers') return new Response(JSON.stringify([cust(1, 'Acme', 'Acme Inc'), cust(2, 'Bob')]), { status: 200 });
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
});

describe('CustomerPicker', () => {
  it('picks an account, using its name as the project label', async () => {
    render(<Harness initial={{ customerId: null, customerText: '' }} />);
    const select = screen.getByRole('combobox', { name: 'Customer' });
    await screen.findByRole('option', { name: 'Acme (Acme Inc)' });
    await userEvent.selectOptions(select, '1');
    expect(changes[changes.length - 1]).toEqual({ customerId: 1, customerText: 'Acme' });
  });

  it('shows a typed name without an account as "Other"', async () => {
    render(<Harness initial={{ customerId: null, customerText: 'Walk-in' }} />);
    await screen.findByRole('option', { name: 'Bob' });
    expect((screen.getByRole('combobox', { name: 'Customer' }) as HTMLSelectElement).value).toBe('__other');
    expect(screen.getByRole('textbox', { name: 'Customer name' })).toHaveProperty('value', 'Walk-in');
  });

  it('creates a new customer inline and selects it', async () => {
    render(<Harness initial={{ customerId: null, customerText: '' }} />);
    await screen.findByRole('option', { name: 'Bob' });
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Customer' }), '__new');
    await userEvent.type(screen.getByRole('textbox', { name: 'New customer name' }), 'Zed');
    await userEvent.type(screen.getByRole('textbox', { name: 'New customer email' }), 'zed@x.test');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(changes[changes.length - 1]).toEqual({ customerId: 9, customerText: 'Zed' }));
    const post = fetchMock.mock.calls.find(([, i]) => i?.method === 'POST')!;
    expect(JSON.parse(post[1].body)).toEqual({ name: 'Zed', email: 'zed@x.test' });
  });

  it('falls back to a plain name box when customers can’t be listed', async () => {
    fetchMock.mockImplementation(async () => new Response('{"detail":"forbidden"}', { status: 403 }));
    render(<Harness initial={{ customerId: null, customerText: 'Someone' }} />);
    expect(await screen.findByRole('textbox', { name: 'Customer name' })).toHaveProperty('value', 'Someone');
    expect(screen.queryByRole('combobox', { name: 'Customer' })).toBeNull();
  });
});
