import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen } from './PrintersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

const mockPrinters = [
  {
    id: 1,
    name: 'Forge',
    printer_type: 'bambu',
    connection_config: { ip_address: '192.168.1.100', access_code: '12345678', serial_number: 'SN001' },
    awaiting_plate_clear: false,
    orca_printer_profiles: [],
    current_orca_printer_profile: null,
    enabled: true,
    connected: true,
  },
];

const mockTypes = [
  {
    printer_type: 'bambu',
    display_name: 'Bambu Lab',
    connection_fields: [
      { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '192.168.1.x', help_text: '' },
      { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null, placeholder: '', help_text: '' },
      { name: 'serial_number', label: 'Serial Number', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    ],
  },
];

function makeFetch(url: string) {
  if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
  if (url === '/api/v1/printers') return Promise.resolve({ ok: true, json: () => Promise.resolve(mockPrinters) });
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => makeFetch(url)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PrintersScreen', () => {
  it('renders fetched printer name', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText('Forge')).toBeTruthy());
  });

  it('shows Add printer button', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByRole('button', { name: /add printer/i })).toBeTruthy());
  });

  it('shows online count in header subtitle', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/1 connected/i)).toBeTruthy());
  });

  it('clicking Add printer shows wizard step 1 with type tiles', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await waitFor(() => expect(screen.getByText('Bambu Lab')).toBeTruthy());
  });

  it('wizard advances to step 2', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await waitFor(() => screen.getByText('Bambu Lab'));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => expect(screen.getByText(/IP Address/i)).toBeTruthy());
  });
});
