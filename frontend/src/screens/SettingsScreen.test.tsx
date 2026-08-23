import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
    if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
    if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

describe('SettingsScreen', () => {
  it('shows only the wired nav items (no General / Notifications / Data & backup)', () => {
    render(<SettingsScreen />, { wrapper });
    expect(screen.getAllByText('Tags').length).toBeGreaterThan(0);
    expect(screen.getByText('Print defaults')).toBeTruthy();
    expect(screen.getByText('Spoolman')).toBeTruthy();
    expect(screen.getByText('About')).toBeTruthy();
    expect(screen.queryByText('General')).toBeNull();
    expect(screen.queryByText('Notifications')).toBeNull();
    expect(screen.queryByText('Data & backup')).toBeNull();
  });

  it('Print defaults shows the wired queue-check-interval control', async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /print defaults/i }));
    await waitFor(() => expect(screen.getByText('Queue check interval')).toBeTruthy());
  });

  it('Print defaults Display name field loads, saves on blur, and clears when blanked', async () => {
    const user = userEvent.setup();
    const putBodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/settings/queue') && init?.method === 'PUT') {
        putBodies.push(JSON.parse(init.body as string));
        return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      }
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: 'Workshop Lead' }), { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /print defaults/i }));

    const input = await screen.findByPlaceholderText('e.g. Workshop Lead') as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('Workshop Lead'));

    await user.clear(input);
    input.blur();

    await waitFor(() => expect(putBodies).toContainEqual({ operator_name: '' }));
  });

  it('About page renders the injected app version and no Released/Channel tiles', async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /about/i }));
    await waitFor(() => expect(screen.getByText('0.1.0')).toBeTruthy());
    expect(screen.queryByText('Released')).toBeNull();
    expect(screen.queryByText('Channel')).toBeNull();
  });

  it('has no aside sub-nav column', () => {
    const { container } = render(<SettingsScreen />, { wrapper });
    expect(container.querySelector('aside')).toBeNull();
  });

  it('has mobile settings tab bar with page buttons', () => {
    const { container } = render(<SettingsScreen />, { wrapper });
    const tabBar = container.querySelector('.settings-tabs');
    expect(tabBar).not.toBeNull();
    expect(tabBar!.querySelector('[class*="settings-tab"]')).not.toBeNull();
  });

  it('renders estimates_enabled toggle in queue settings', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({
        check_interval_minutes: 5,
        operator_name: null,
        snapshot_interval_seconds: 2,
        estimates_enabled: false,
      }), { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /print defaults/i }));

    await waitFor(() => {
      expect(screen.getByText(/enable estimate generation/i)).toBeInTheDocument();
    });
  });

  it('ApiKeysPage: revoke requires confirm, API call not made if cancelled', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    let revokeCalled = false;

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys') && url.includes('revoke')) {
        revokeCalled = true;
        return new Response('{}', { status: 200 });
      }
      if (url.includes('/api/v1/api-keys')) {
        return new Response(JSON.stringify([{
          id: 1,
          name: 'Test Key',
          key_prefix: 'test_',
          scopes: ['jobs:read'],
          created_at: '2026-01-01T00:00:00Z',
          last_used_at: null,
          enabled: true,
        }]), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));

    await waitFor(() => expect(screen.getByText('Test Key')).toBeInTheDocument());

    const revokeButton = screen.getByRole('button', { name: /revoke/i });
    await user.click(revokeButton);

    expect(confirmSpy).toHaveBeenCalledWith('Revoke access for the key "Test Key"?');
    expect(revokeCalled).toBe(false);

    confirmSpy.mockRestore();
  });

  it('ApiKeysPage: Create key button disabled until scopes selected', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));

    // Click the Create key button in the header
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);

    // Fill in the name field
    const nameInput = screen.getByPlaceholderText(/e\.g\. Ordinus/i) as HTMLInputElement;
    await user.type(nameInput, 'Test Key');

    // Get the modal Create button (should still be disabled - no scopes)
    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1] as HTMLButtonElement;
    expect(modalCreateButton.disabled).toBe(true);

    // Select a scope to enable the button
    const firstCheckbox = screen.getAllByRole('checkbox')[0];
    await user.click(firstCheckbox);

    // Button should now be enabled
    expect(modalCreateButton.disabled).toBe(false);
  });

  it('ApiKeysPage: "All scopes" preset enables Create button', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'files:write', 'jobs:read', 'jobs:write', 'printers:read', 'printers:write', 'printers:control', 'queue:read', 'queue:write', 'fleet:read'];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);
    await user.type(screen.getByPlaceholderText(/e\.g\. Ordinus/i), 'Test Key');

    const allScopesButton = screen.getByRole('button', { name: /all scopes/i });
    await user.click(allScopesButton);

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1] as HTMLButtonElement;
    await waitFor(() => expect(modalCreateButton.disabled).toBe(false));
  });

  it('ApiKeysPage: "Read-only" preset enables Create button with subset of scopes', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'files:write', 'jobs:read', 'jobs:write', 'printers:read', 'printers:write', 'printers:control', 'queue:read', 'queue:write', 'fleet:read'];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);
    await user.type(screen.getByPlaceholderText(/e\.g\. Ordinus/i), 'Test Key');

    const readOnlyButton = screen.getByRole('button', { name: /read-only/i });
    await user.click(readOnlyButton);

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1] as HTMLButtonElement;
    await waitFor(() => expect(modalCreateButton.disabled).toBe(false));
  });

  it('CreateKeyModal successful submission: calls createApiKey and fires onCreated', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'jobs:read', 'printers:read', 'queue:read'];
    let createCalled = false;
    let createArgs: { name: string; scopes: string[] } | null = null;

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys') && init?.method === 'POST') {
        createCalled = true;
        createArgs = JSON.parse(init.body as string);
        return new Response(JSON.stringify({
          id: 1,
          name: 'Test Key',
          key: 'thms_secret_key_12345',
          key_prefix: 'thms_',
          scopes: ['files:read', 'jobs:read'],
          created_at: '2026-01-01T00:00:00Z',
        }), { status: 201 });
      }
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);

    const nameInput = screen.getByPlaceholderText(/e\.g\. Ordinus/i) as HTMLInputElement;
    await user.type(nameInput, 'Test Key');

    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]); // Select first scope
    await user.click(checkboxes[1]); // Select second scope

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1];
    await user.click(modalCreateButton);

    await waitFor(() => expect(createCalled).toBe(true));
    expect(createArgs).toEqual({ name: 'Test Key', scopes: expect.any(Array) });
    await waitFor(() => expect(screen.getByText(/Key created/)).toBeInTheDocument());
  });

  it('RevealKeyDialog: renders key field and copy button works', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'jobs:read'];
    const clipboardSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          id: 1,
          name: 'Test Key',
          key: 'thms_secret_key_12345',
          key_prefix: 'thms_',
          scopes: ['files:read', 'jobs:read'],
          created_at: '2026-01-01T00:00:00Z',
        }), { status: 201 });
      }
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);

    await user.type(screen.getByPlaceholderText(/e\.g\. Ordinus/i), 'Test Key');
    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]);

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1];
    await user.click(modalCreateButton);

    await waitFor(() => expect(screen.getByText(/Key created/)).toBeInTheDocument());

    const keyField = screen.getByDisplayValue('thms_secret_key_12345') as HTMLInputElement;
    expect(keyField).toBeInTheDocument();
    expect(keyField.readOnly).toBe(true);

    const copyButton = screen.getByRole('button', { name: /copy/i });
    await user.click(copyButton);

    expect(clipboardSpy).toHaveBeenCalledWith('thms_secret_key_12345');

    clipboardSpy.mockRestore();
  });

  it('RevealKeyDialog: Done button closes dialog', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'jobs:read'];

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          id: 1,
          name: 'Test Key',
          key: 'thms_secret_key_12345',
          key_prefix: 'thms_',
          scopes: ['files:read', 'jobs:read'],
          created_at: '2026-01-01T00:00:00Z',
        }), { status: 201 });
      }
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);

    await user.type(screen.getByPlaceholderText(/e\.g\. Ordinus/i), 'Test Key');
    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]);

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1];
    await user.click(modalCreateButton);

    await waitFor(() => expect(screen.getByText(/Key created/)).toBeInTheDocument());

    const doneButton = screen.getByRole('button', { name: /Done/i });
    await user.click(doneButton);

    await waitFor(() => expect(screen.queryByText(/Key created/)).not.toBeInTheDocument());
  });

  it('Revoke SUCCESS path: confirms, makes API call, and refetches list', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    let revokeCalled = false;

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys') && url.includes('revoke') && init?.method === 'POST') {
        revokeCalled = true;
        return new Response('{}', { status: 200 });
      }
      if (url.includes('/api/v1/api-keys')) {
        return new Response(JSON.stringify([{
          id: 1,
          name: 'Test Key',
          key_prefix: 'test_',
          scopes: ['jobs:read'],
          created_at: '2026-01-01T00:00:00Z',
          last_used_at: null,
          enabled: revokeCalled ? false : true,
        }]), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));

    await waitFor(() => expect(screen.getByText('Test Key')).toBeInTheDocument());

    const revokeButton = screen.getByRole('button', { name: /revoke/i });
    await user.click(revokeButton);

    expect(confirmSpy).toHaveBeenCalledWith('Revoke access for the key "Test Key"?');
    expect(revokeCalled).toBe(true);

    // After revoke, the key should show as Revoked and have Delete button instead of Revoke
    await waitFor(() => expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /revoke/i })).not.toBeInTheDocument();

    confirmSpy.mockRestore();
  });

  it('Create key error handling: failed API call shows error in modal', async () => {
    const user = userEvent.setup();
    const allScopes = ['files:read', 'jobs:read'];

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/api/v1/api-keys/scopes')) return new Response(JSON.stringify(allScopes), { status: 200 });
      if (url.includes('/api/v1/api-keys') && init?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'Duplicate key name' }), { status: 400 });
      }
      if (url.includes('/api/v1/api-keys')) return new Response('[]', { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /api keys/i }));
    const headerCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    await user.click(headerCreateButtons[0]);

    await user.type(screen.getByPlaceholderText(/e\.g\. Ordinus/i), 'Test Key');
    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]);

    const allCreateButtons = screen.getAllByRole('button', { name: /create key/i });
    const modalCreateButton = allCreateButtons[allCreateButtons.length - 1];
    await user.click(modalCreateButton);

    await waitFor(() => expect(screen.getByText(/Duplicate key name/i)).toBeInTheDocument());
  });

  it('SpoolmanPage: Sync now button calls sync-now endpoint and shows success message', async () => {
    const user = userEvent.setup();
    let syncCalled = false;

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/api/v1/spoolman/sync-now')) {
        syncCalled = true;
        return new Response(JSON.stringify({ filament_count: 5, spool_count: 12 }), { status: 200 });
      }
      if (url.includes('/settings/spoolman/test')) {
        return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
      }
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: true, url: 'http://spoolman.test', api_key: null }), { status: 200 });
      if (url.includes('/api/v1/spoolman/spools')) return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /spoolman/i }));

    // Test connection to mark as connected
    const testButton = screen.getByRole('button', { name: /test connection/i });
    await user.click(testButton);

    // Wait for sync button to appear (only visible when connected)
    await waitFor(() => expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument());

    syncCalled = false;
    // Click Sync now
    const syncButton = screen.getByRole('button', { name: /sync now/i });
    await user.click(syncButton);

    // Check that sync-now endpoint was called
    expect(syncCalled).toBe(true);

    // Check for success message
    await waitFor(() => expect(screen.getByText(/Synced — 5 filaments, 12 spools/i)).toBeInTheDocument());
  });

  it('SpoolmanPage: Sync now button shows error message on failure', async () => {
    const user = userEvent.setup();

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      if (url.includes('/api/v1/spoolman/sync-now')) {
        return new Response(JSON.stringify({ detail: 'Connection refused' }), { status: 503 });
      }
      if (url.includes('/settings/spoolman/test')) {
        return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
      }
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: true, url: 'http://spoolman.test', api_key: null }), { status: 200 });
      if (url.includes('/api/v1/spoolman/spools')) return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /spoolman/i }));

    // Test connection to mark as connected
    const testButton = screen.getByRole('button', { name: /test connection/i });
    await user.click(testButton);

    // Wait for sync button to appear
    await waitFor(() => expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument());

    // Click Sync now
    const syncButton = screen.getByRole('button', { name: /sync now/i });
    await user.click(syncButton);

    // Check for error message
    await waitFor(() => expect(screen.getByText(/Sync failed.*503/i)).toBeInTheDocument());
  });
});
