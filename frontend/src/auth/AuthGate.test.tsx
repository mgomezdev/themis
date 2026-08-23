import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { getApiKey } from './apiKeyStore';

// AuthGate caches its bootstrap POST in a module-level promise (so React StrictMode's
// dev-only double-invoke of effects doesn't fire it twice) — reset the module between
// tests so each one gets its own fresh bootstrap attempt.
beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe('AuthGate', () => {
  it('renders children immediately when a key is already stored', async () => {
    localStorage.setItem('themis.apiKey', 'thm_existing_key');
    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);
    expect(screen.getByText('protected content')).toBeTruthy();
  });

  it('bootstraps a key via POST and stores it when none is stored', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        id: 1, name: 'Browser', key_prefix: 'thm_abc123', scopes: ['files:read'],
        enabled: true, created_at: '2026-01-01T00:00:00', last_used_at: null,
        revoked_at: null, key: 'thm_bootstrapped_key',
      }), { status: 200 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(getApiKey()).toBe('thm_bootstrapped_key');
    expect(fetch).toHaveBeenCalledWith('/api/v1/api-keys', expect.objectContaining({ method: 'POST' }));
  });

  it('shows the manual-entry form when bootstrap 401s', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());
    expect(screen.queryByText('protected content')).toBeNull();
    expect(getApiKey()).toBeNull();
  });

  it('accepts a valid key (200) via manual entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      // bootstrap 401s → show form
      if (opts?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 });
      }
      // manual key validation succeeds
      return new Response(JSON.stringify({ id: 1 }), { status: 200 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    // Wait for form to appear
    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());

    // Manually imported here after module reset
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    const input = screen.getByPlaceholderText('thm_...');
    const button = screen.getByRole('button', { name: /continue/i });

    await user.type(input, 'thm_test_valid_key');
    await user.click(button);

    // Key accepted, state should be 'ready'
    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(getApiKey()).toBe('thm_test_valid_key');
  });

  it('accepts a valid key (403) via manual entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      if (opts?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 });
      }
      // 403: valid key, lacks apikeys:read scope
      return new Response(JSON.stringify({}), { status: 403 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());

    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    const input = screen.getByPlaceholderText('thm_...');
    const button = screen.getByRole('button', { name: /continue/i });

    await user.type(input, 'thm_test_key_403');
    await user.click(button);

    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(getApiKey()).toBe('thm_test_key_403');
  });

  it('rejects invalid key (401) and shows error', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      if (opts?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 });
      }
      // Invalid key
      return new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());

    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    const input = screen.getByPlaceholderText('thm_...');
    const button = screen.getByRole('button', { name: /continue/i });

    await user.type(input, 'thm_invalid_key');
    await user.click(button);

    // Error should appear
    await waitFor(() => expect(screen.getByText('API key not recognized')).toBeTruthy());
    // Children should NOT render
    expect(screen.queryByText('protected content')).toBeNull();
    // Key should NOT be stored
    expect(getApiKey()).toBeNull();
  });

  it('shows server error on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      if (opts?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 });
      }
      // Server error
      return new Response(JSON.stringify({}), { status: 500 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());

    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    const input = screen.getByPlaceholderText('thm_...');
    const button = screen.getByRole('button', { name: /continue/i });

    await user.type(input, 'thm_key');
    await user.click(button);

    await waitFor(() => expect(screen.getByText('Server unreachable')).toBeTruthy());
    expect(screen.queryByText('protected content')).toBeNull();
    expect(getApiKey()).toBeNull();
  });

  it('bootstrap 400 shows neutral message, no error or retry button', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Table not empty' }), { status: 400 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());
    expect(screen.getByText(/Themis couldn't automatically set up access/i)).toBeTruthy();
    expect(screen.queryByText(/Couldn't reach the Themis server/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });

  it('bootstrap 500 shows error message and retry button', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Internal error' }), { status: 500 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Couldn't reach the Themis server/i)).toBeTruthy());
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
    expect(screen.queryByText(/Themis couldn't automatically set up access/i)).toBeNull();
  });

  it('clicking retry re-triggers bootstrap fetch', async () => {
    let callCount = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      callCount++;
      // First two calls (network error, then retry) fail
      if (callCount <= 2) {
        return new Response(JSON.stringify({ detail: 'Server error' }), { status: 500 });
      }
      // Third call succeeds
      return new Response(JSON.stringify({
        id: 1, name: 'Browser', key_prefix: 'thm_abc123', scopes: ['files:read'],
        enabled: true, created_at: '2026-01-01T00:00:00', last_used_at: null,
        revoked_at: null, key: 'thm_bootstrap_retry_key',
      }), { status: 200 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    // Wait for error state
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy());
    expect(callCount).toBe(1);

    // Click retry
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    await user.click(retryBtn);

    // Second attempt should also fail (but triggers another fetch)
    await waitFor(() => {
      expect(callCount).toBe(2);
      expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
    });

    // Click retry again
    await user.click(screen.getByRole('button', { name: /retry/i }));

    // Third attempt succeeds
    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(callCount).toBe(3);
    expect(getApiKey()).toBe('thm_bootstrap_retry_key');
  });
});
