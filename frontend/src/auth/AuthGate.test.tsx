import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { getApiKey } from './apiKeyStore';

// Fresh module per test so handler registration and state don't leak between tests.
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

  it('lets a local-network device in without a key and never mints one', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ local: true, role: 'admin', customer: null }), { status: 200 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(getApiKey()).toBeNull();
    expect(fetch).toHaveBeenCalledWith('/api/v1/auth/me');
    expect(fetch).not.toHaveBeenCalledWith('/api/v1/api-keys', expect.objectContaining({ method: 'POST' }));
  });

  it('shows the sign-in form to a signed-out visitor', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByPlaceholderText('Email or username')).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(screen.getByText(/Enter your API key/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
    expect(screen.queryByText('protected content')).toBeNull();
  });

  it('signs in as admin and stores the session key', async () => {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === '/api/v1/auth/login')
        return new Response(JSON.stringify({ key: 'thm_admin_session', customer: null, admin: true }), { status: 200 });
      return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    await user.type(await screen.findByPlaceholderText('Email or username'), 'admin');
    await user.type(screen.getByPlaceholderText('Password'), 's3cret');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());
    expect(getApiKey()).toBe('thm_admin_session');
    const [, init] = fetchMock.mock.calls.find(([u]) => u === '/api/v1/auth/login')! as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ email: 'admin', password: 's3cret' });
  });

  it('admin recovery: requests a log code, then sets a new password', async () => {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === '/api/v1/auth/recover')
        return new Response(JSON.stringify({ detail: 'ok' }), { status: 202 });
      if (url === '/api/v1/auth/recover/confirm')
        return new Response(JSON.stringify({ detail: 'ok' }), { status: 200 });
      return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /forgot admin password/i }));
    await user.click(screen.getByRole('button', { name: /write a one-time code/i }));
    await waitFor(() => expect(screen.getByText(/written to the Themis server log/i)).toBeTruthy());

    await user.type(screen.getByPlaceholderText('Recovery code'), 'ABCDE-FGHJK');
    await user.type(screen.getByPlaceholderText('New admin password'), 'newpw');
    await user.click(screen.getByRole('button', { name: /set admin password/i }));

    await waitFor(() => expect(screen.getByText(/Admin password set/i)).toBeTruthy());
    expect((screen.getByPlaceholderText('Email or username') as HTMLInputElement).value).toBe('admin');
    const [, init] = fetchMock.mock.calls.find(([u]) => u === '/api/v1/auth/recover/confirm')! as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ code: 'ABCDE-FGHJK', password: 'newpw' });
  });

  it('admin recovery: shows an error for a wrong or expired code', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/v1/auth/recover/confirm')
        return new Response(JSON.stringify({ detail: 'Invalid or expired recovery code' }), { status: 400 });
      return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
    }));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /forgot admin password/i }));
    await user.type(screen.getByPlaceholderText('Recovery code'), 'WRONG-CODE0');
    await user.type(screen.getByPlaceholderText('New admin password'), 'x');
    await user.click(screen.getByRole('button', { name: /set admin password/i }));

    await waitFor(() => expect(screen.getByText('Invalid or expired recovery code')).toBeTruthy());
  });

  it('shows the manual-entry form when the session check fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Missing or invalid API key' }), { status: 401 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Enter your API key/i)).toBeTruthy());
    expect(screen.queryByText('protected content')).toBeNull();
    expect(getApiKey()).toBeNull();
  });

  it('accepts a valid key (200) via manual entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, _opts?: RequestInit) => {
      // signed-out visitor → sign-in / key form
      if (url === '/api/v1/auth/me') {
        return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
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
    // Validated with the header the backend actually reads.
    const call = (fetch as any).mock.calls.find(([u]: [string]) => u === '/api/v1/api-keys');
    expect(new Headers(call[1].headers).get('X-Api-Key')).toBe('thm_test_valid_key');
  });

  it('accepts a valid key (403) via manual entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, _opts?: RequestInit) => {
      // signed-out visitor → sign-in / key form
      if (url === '/api/v1/auth/me') {
        return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
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
    vi.stubGlobal('fetch', vi.fn(async (url: string, _opts?: RequestInit) => {
      // signed-out visitor → sign-in / key form
      if (url === '/api/v1/auth/me') {
        return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
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
    vi.stubGlobal('fetch', vi.fn(async (url: string, _opts?: RequestInit) => {
      // signed-out visitor → sign-in / key form
      if (url === '/api/v1/auth/me') {
        return new Response(JSON.stringify({ local: false, role: null, customer: null }), { status: 200 });
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

  it('session-check 500 shows error message and retry button', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Internal error' }), { status: 500 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText(/Couldn't reach the Themis server/i)).toBeTruthy());
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('clicking retry re-checks the session', async () => {
    let callCount = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      callCount++;
      // First two calls (network error, then retry) fail
      if (callCount <= 2) {
        return new Response(JSON.stringify({ detail: 'Server error' }), { status: 500 });
      }
      // Third call succeeds (local-network admin)
      return new Response(JSON.stringify({ local: true, role: 'admin', customer: null }), { status: 200 });
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
  });

  it('shows forbidden toast when apiFetch gets 403 from authenticated call', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ local: true, role: 'admin', customer: null }), { status: 200 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    // Wait for ready state
    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());

    // Now simulate a 403 from apiFetch by changing fetch mock
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Missing files:write scope' }), { status: 403 })));

    // Call apiFetch to trigger the forbiddenHandler
    const { apiFetch } = await import('../api/client');
    await apiFetch('/api/test');

    // Toast should appear with the detail message
    await waitFor(() => expect(screen.getByText('Missing files:write scope')).toBeTruthy());
  });

  it('shows forbidden toast with generic message on 403 without detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ local: true, role: 'admin', customer: null }), { status: 200 })));

    const { AuthGate } = await import('./AuthGate');
    render(<AuthGate><div>protected content</div></AuthGate>);

    await waitFor(() => expect(screen.getByText('protected content')).toBeTruthy());

    // Mock 403 without detail
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({}), { status: 403 })));

    const { apiFetch } = await import('../api/client');
    await apiFetch('/api/test');

    // Toast should appear with generic message
    await waitFor(() => expect(screen.getByText(/doesn't have permission/i)).toBeTruthy());
  });
});
