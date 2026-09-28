import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProjectDetailScreen } from './ProjectDetailScreen';

const BASE_PROJECT = {
  id: 42, name: 'Test Project', customer: '', order_type: 'internal', on_hold: false,
  due_date: null, notes: null, result_file_id: null, source_app: null, source_user: null,
  source_layout_id: null, created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z',
  items: [], links: [], parts: [], jobs_total: 0, jobs_complete: 0,
  estimate_filament_grams_total: null, estimate_seconds_total: null,
  estimate_filament_grams_remaining: null, estimate_seconds_remaining: null,
  actual_filament_grams: null, actual_seconds: null,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={['/projects/42']}>
      <Routes>
        <Route path="/projects/:id" element={<ProjectDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockFetch(shareState: { enabled: boolean; token: string | null; created_at?: string | null }) {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.includes('/share')) {
      return new Response(JSON.stringify(shareState), { status: 200 });
    }
    if (url.includes('/jobs')) {
      return new Response('[]', { status: 200 });
    }
    if (url.match(/\/api\/v1\/projects\/42$/)) {
      return new Response(JSON.stringify(BASE_PROJECT), { status: 200 });
    }
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('ProjectDetailScreen share panel', () => {
  it('does not fetch share state until the Share panel is opened', async () => {
    const fetchMock = mockFetch({ enabled: false, token: null });
    vi.stubGlobal('fetch', fetchMock);

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));

    expect(fetchMock.mock.calls.some(([url]) => url.includes('/share'))).toBe(false);
  });

  it('shows a Create share link button when no link exists', async () => {
    vi.stubGlobal('fetch', mockFetch({ enabled: false, token: null }));

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));

    expect(await screen.findByRole('button', { name: /create share link/i })).toBeTruthy();
  });

  it('shows the share URL, "Shared since", and Copy/Regenerate/Revoke when a link exists', async () => {
    vi.stubGlobal('fetch', mockFetch({ enabled: true, token: 'abc123', created_at: '2026-09-06T00:00:00Z' }));

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));

    await waitFor(() => expect(screen.getByDisplayValue(/abc123/)).toBeTruthy());
    expect(screen.getByText(/shared since/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /copy/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /revoke/i })).toBeTruthy();
  });

  it('clicking Create share link calls PUT and displays the new token', async () => {
    const fetchMock = mockFetch({ enabled: false, token: null });
    vi.stubGlobal('fetch', fetchMock);

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));
    await userEvent.click(await screen.findByRole('button', { name: /create share link/i }));

    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(putCall).toBeTruthy();
    expect(putCall![0]).toBe('/api/v1/projects/42/share');
  });
});
