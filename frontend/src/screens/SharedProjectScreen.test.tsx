import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { SharedProjectScreen } from './SharedProjectScreen';

function renderAtToken(token: string) {
  return render(
    <MemoryRouter initialEntries={[`/share/${token}`]}>
      <Routes>
        <Route path="/share/:token" element={<SharedProjectScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('SharedProjectScreen', () => {
  it('renders project name, customer, and progress on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      name: 'Public Project', customer: 'Acme Co', due_date: '2026-12-01', on_hold: false,
      items: [{ name: 'part.3mf', quantity: 3, quantity_completed: 1 }],
      parts: [{ name: 'M3 bolt', quantity: 4 }],
      links: [{ url: 'https://example.com', label: 'Spec sheet' }],
      jobs_total: 3, jobs_complete: 1, estimate_seconds_remaining: 7200,
      updated_at: '2026-09-06T00:00:00Z',
    }), { status: 200 })));

    renderAtToken('valid-token');

    await waitFor(() => expect(screen.getByText('Public Project')).toBeTruthy());
    expect(screen.getByText('Acme Co')).toBeTruthy();
    expect(screen.getByText(/part\.3mf/)).toBeTruthy();
    expect(screen.getByText(/M3 bolt/)).toBeTruthy();
    expect(screen.getByText(/Spec sheet/)).toBeTruthy();
  });

  it('shows a not-found message on 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 404 })));

    renderAtToken('revoked-token');

    await waitFor(() => expect(screen.getByText(/invalid or has been revoked/i)).toBeTruthy());
  });

  it('does not send an X-Api-Key header', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({
      name: 'P', customer: '', due_date: null, on_hold: false,
      items: [], parts: [], links: [], jobs_total: 0, jobs_complete: 0,
      estimate_seconds_remaining: null, updated_at: '2026-09-06T00:00:00Z',
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('themis.apiKey', 'thm_should_not_be_sent');

    renderAtToken('valid-token');

    await waitFor(() => expect(screen.getByText('P')).toBeTruthy());
    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has('X-Api-Key')).toBe(false);
    localStorage.removeItem('themis.apiKey');
  });
});
