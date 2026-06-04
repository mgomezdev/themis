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
    if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5 }), { status: 200 });
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
});
