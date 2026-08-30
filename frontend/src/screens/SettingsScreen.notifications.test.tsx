// frontend/src/screens/SettingsScreen.notifications.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const EMPTY_NOTIFICATION_CONFIG = {
  ntfy: { enabled: false, server_url: null, topic: null, priority: null, events: [] },
  discord: { enabled: false, webhook_url: null, events: [] },
  email: {
    enabled: false, host: null, port: null, username: null, password: null,
    from_addr: null, to_addrs: [], events: [],
  },
};

function stubFetch(overrides?: (url: string, init?: RequestInit) => Response | undefined) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const custom = overrides?.(url, init);
    if (custom) return custom;
    if (url.includes('/settings/notifications')) {
      return new Response(JSON.stringify(EMPTY_NOTIFICATION_CONFIG), { status: 200 });
    }
    if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
    return new Response('{}', { status: 200 });
  }));
}

beforeEach(() => { vi.restoreAllMocks(); stubFetch(); });

describe('Settings → Notifications page', () => {
  it('nav item renders and navigates to the page', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsScreen /></MemoryRouter>);
    const navButton = screen.getByRole('button', { name: /notifications/i });
    await user.click(navButton);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'ntfy' })).toBeInTheDocument());
  });

  it('renders three channel cards with their fields', async () => {
    render(<MemoryRouter initialEntries={['/settings/notifications']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByPlaceholderText('https://ntfy.sh')).toBeInTheDocument());
    expect(screen.getByPlaceholderText(/discord\.com\/api\/webhooks/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('smtp.example.com')).toBeInTheDocument();
  });

  it('toggling a channel enable switch works', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={['/settings/notifications']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByPlaceholderText('https://ntfy.sh')).toBeInTheDocument());

    const switches = screen.getAllByRole('switch');
    // First switch on the page is ntfy's enable toggle.
    const ntfyToggle = switches[0];
    expect(ntfyToggle.getAttribute('aria-checked')).toBe('false');
    await user.click(ntfyToggle);
    expect(ntfyToggle.getAttribute('aria-checked')).toBe('true');
  });

  it('Save calls saveNotificationConfig / PUT with the assembled config', async () => {
    const user = userEvent.setup();
    const putBodies: unknown[] = [];
    stubFetch((url, init) => {
      if (url.includes('/settings/notifications') && init?.method === 'PUT') {
        putBodies.push(JSON.parse(init.body as string));
        return new Response(JSON.stringify(EMPTY_NOTIFICATION_CONFIG), { status: 200 });
      }
      return undefined;
    });

    render(<MemoryRouter initialEntries={['/settings/notifications']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByPlaceholderText('https://ntfy.sh')).toBeInTheDocument());

    const urlInput = screen.getByPlaceholderText('https://ntfy.sh') as HTMLInputElement;
    await user.type(urlInput, 'https://ntfy.example.com');

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    await user.click(saveButton);

    await waitFor(() => expect(putBodies.length).toBe(1));
    const body = putBodies[0] as { ntfy: { server_url: string } };
    expect(body.ntfy.server_url).toBe('https://ntfy.example.com');
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });

  it('Send test calls the test endpoint with the channel current form values and shows the result', async () => {
    const user = userEvent.setup();
    let testBody: unknown = null;
    stubFetch((url, init) => {
      if (url.includes('/settings/notifications/test') && init?.method === 'POST') {
        testBody = JSON.parse(init.body as string);
        return new Response(JSON.stringify({ ok: true, message: 'Test push sent' }), { status: 200 });
      }
      return undefined;
    });

    render(<MemoryRouter initialEntries={['/settings/notifications']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByPlaceholderText('https://ntfy.sh')).toBeInTheDocument());

    const urlInput = screen.getByPlaceholderText('https://ntfy.sh') as HTMLInputElement;
    await user.type(urlInput, 'https://ntfy.example.com');
    const topicInput = screen.getByPlaceholderText('themis-alerts') as HTMLInputElement;
    await user.type(topicInput, 'my-topic');

    const testButtons = screen.getAllByRole('button', { name: /send test/i });
    await user.click(testButtons[0]);

    await waitFor(() => expect(testBody).toBeTruthy());
    const body = testBody as { channel: string; config: { server_url: string; topic: string } };
    expect(body.channel).toBe('ntfy');
    expect(body.config.server_url).toBe('https://ntfy.example.com');
    expect(body.config.topic).toBe('my-topic');

    await waitFor(() => expect(screen.getByText('Test push sent')).toBeInTheDocument());
  });
});
