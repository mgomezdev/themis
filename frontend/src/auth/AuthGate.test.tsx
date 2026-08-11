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
});
