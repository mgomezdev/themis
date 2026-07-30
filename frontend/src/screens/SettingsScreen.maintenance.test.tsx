// frontend/src/screens/SettingsScreen.maintenance.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/maintenance/items' && (!init || init.method === undefined)) {
      return new Response(JSON.stringify([{
        id: 1, name: 'Wash build plate', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ id: 1, trigger_type: 'job_count', amount: 10, unit: null }],
      }]), { status: 200 });
    }
    if (url === '/api/v1/maintenance/items' && init?.method === 'POST') {
      return new Response(JSON.stringify({
        id: 2, name: 'Clean fans', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }],
      }), { status: 201 });
    }
    if (url === '/api/v1/maintenance/templates') {
      return new Response(JSON.stringify([
        { name: 'Clean fans', description: 'Blow out dust.', triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }] },
      ]), { status: 200 });
    }
    if (url === '/api/v1/printers/orca-machine-catalog') {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    // Other settings sub-pages' unrelated calls (spoolman config, queue config, etc.)
    return new Response(JSON.stringify({}), { status: 200 });
  }));
}

beforeEach(() => { vi.restoreAllMocks(); stubFetch(); });

describe('Settings → Maintenance page', () => {
  it('lists existing maintenance items', async () => {
    render(<MemoryRouter initialEntries={['/settings/maintenance']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Wash build plate')).toBeInTheDocument());
    expect(screen.getByText(/10 jobs/i)).toBeInTheDocument();
  });

  it('adds a suggested template as a new item', async () => {
    render(<MemoryRouter initialEntries={['/settings/maintenance']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Wash build plate')).toBeInTheDocument());

    fireEvent.click(await screen.findByRole('button', { name: /add.*clean fans/i }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(
      (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
        (c: unknown[]) => c[0] === '/api/v1/maintenance/items' && (c[1] as RequestInit)?.method === 'POST'
      )
    ).toBe(true));
  });
});
