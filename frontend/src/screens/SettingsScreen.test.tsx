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
});
