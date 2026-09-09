import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  vi.restoreAllMocks();
  // Deliberately do NOT seed an API key - the whole point of this route is that
  // it works without one.
});

afterEach(() => {
  window.history.pushState({}, '', '/');
});

describe('App - public share route', () => {
  it('renders the shared project page at /share/:token without going through AuthGate', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (typeof url === 'string' && url.includes('/api/v1/public/projects/')) {
        return new Response(JSON.stringify({
          name: 'Public Project', customer: '', due_date: null, on_hold: false,
          items: [], parts: [], links: [], jobs_total: 0, jobs_complete: 0,
          estimate_seconds_remaining: null, updated_at: '2026-09-06T00:00:00Z',
        }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    }));

    window.history.pushState({}, '', '/share/some-token');
    render(<App />);

    await waitFor(() => expect(screen.getByText('Public Project')).toBeTruthy());
    // AuthGate's "Connecting…" / API-key prompt must never appear on this route.
    expect(screen.queryByText(/Connecting/i)).toBeNull();
    expect(screen.queryByText(/Enter your API key/i)).toBeNull();
  });
});
