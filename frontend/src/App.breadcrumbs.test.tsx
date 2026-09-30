import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import App from './App';

const PROJECT = {
  id: 42, name: 'Gridfinity Set', customer: '', order_type: 'customer', on_hold: false,
  due_date: null, notes: null, result_file_id: null, source_app: null, source_user: null,
  source_layout_id: null, amount_paid: 20, price: 50, payment_status: 'partial', stage: 'queued',
  customer_id: 7, customer_name: 'Acme Corp',
  created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z',
  items: [], links: [], parts: [], jobs_total: 0, jobs_complete: 0,
  estimate_filament_grams_total: null, estimate_seconds_total: null,
  estimate_filament_grams_remaining: null, estimate_seconds_remaining: null,
  actual_filament_grams: null, actual_seconds: null, filament_cost_total: null,
};

function stubFetch(project: object) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (/\/api\/v1\/projects\/42$/.test(url)) return new Response(JSON.stringify(project), { status: 200 });
    return new Response('[]', { status: 200 });
  }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.setItem('themis.apiKey', 'thm_test_key');
  class FakeWS { onmessage = null; onopen = null; onclose = null; close() {} send() {} }
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);
});

describe('Project detail breadcrumbs', () => {
  it('end with the linked customer name and the project name', async () => {
    stubFetch(PROJECT);
    window.history.pushState({}, '', '/projects/42');
    const { container } = render(<App />);
    const topbar = () => container.querySelector('.topbar') as HTMLElement;

    await waitFor(() => expect(within(topbar()).getByRole('heading', { name: 'Gridfinity Set' })).toBeTruthy());
    const crumbs = Array.from(topbar().querySelectorAll('.crumb')).map(c => c.textContent);
    expect(crumbs).toEqual(['Workshop', 'Projects', 'Acme Corp']);
    expect(within(topbar()).getByRole('link', { name: 'Acme Corp' }).getAttribute('href')).toBe('/customers/7');
    expect(within(topbar()).getByRole('link', { name: 'Projects' }).getAttribute('href')).toBe('/projects');
  });

  it('omit the customer crumb for an internal project', async () => {
    stubFetch({ ...PROJECT, customer_id: null, customer_name: null, order_type: 'internal' });
    window.history.pushState({}, '', '/projects/42');
    const { container } = render(<App />);
    const topbar = () => container.querySelector('.topbar') as HTMLElement;

    await waitFor(() => expect(within(topbar()).getByRole('heading', { name: 'Gridfinity Set' })).toBeTruthy());
    const crumbs = Array.from(topbar().querySelectorAll('.crumb')).map(c => c.textContent);
    expect(crumbs).toEqual(['Workshop', 'Projects']);
  });
});

describe('Customers route', () => {
  it('is a top-level nav link and the old settings path redirects to it', async () => {
    stubFetch(PROJECT);
    window.history.pushState({}, '', '/settings/customers');
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe('/customers'));
    expect(screen.getAllByRole('link', { name: /Customers/ }).some(a => a.getAttribute('href') === '/customers')).toBe(true);
  });
});
