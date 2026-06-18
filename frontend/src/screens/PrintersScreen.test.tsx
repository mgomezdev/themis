import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen, EditForm } from './PrintersScreen';
import type { ApiPrinter, PrinterType } from '../api/printers';

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
    loaded_filaments: [],
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
  if (url.includes('/orca-machine-catalog')) return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  if (url.includes('/spoolman')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false, url: null, api_key: null }) });
  if (url.includes('/profiles')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ print_profiles: [], filament_profiles: [] }) });
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

  it('shows no-filament placeholder when loaded_filaments is empty', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/— no filament/i)).toBeTruthy());
  });

  it('shows filament swatch when loaded_filaments is populated', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([{
        ...mockPrinters[0],
        loaded_filaments: [{ slot: 0, filament_id: null, name: 'Bambu PLA', type: 'PLA', color: '#ff0000' }],
      }]) });
    }));
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(document.querySelector('[title="Bambu PLA (PLA)"]')).toBeTruthy());
  });

  it('shows loaded filaments section in edit form', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => expect(screen.getByText(/loaded filaments/i)).toBeTruthy());
  });

  it('can add a slot in the edit form', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/loaded filaments/i));
    await user.click(screen.getByRole('button', { name: /add slot/i }));
    await waitFor(() => expect(screen.getByPlaceholderText(/filament name/i)).toBeTruthy());
  });

  it('PATCH includes loaded_filaments on save', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      if (init?.method === 'PATCH') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockPrinters[0] }) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockPrinters) });
    }));
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/loaded filaments/i));
    await user.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      const patchCall = calls.find(([, init]) => init?.method === 'PATCH');
      expect(patchCall).toBeTruthy();
      const body = JSON.parse(patchCall![1]!.body as string);
      expect(body).toHaveProperty('loaded_filaments');
      expect(Array.isArray(body.loaded_filaments)).toBe(true);
    });
  });

  it('renders slot with filament_id null without error', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([{
        ...mockPrinters[0],
        loaded_filaments: [{ slot: 0, filament_id: null, name: 'Generic PLA', type: 'PLA', color: '#888888' }],
      }]) });
    }));
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(document.querySelector('[title="Generic PLA (PLA)"]')).toBeTruthy());
  });
});

// ── Integration: EditForm + SlotSpoolPicker spool selection ───────────────────

const INTEGRATION_PRINTER: ApiPrinter = {
  id: 7,
  name: 'Prism',
  printer_type: 'bambu',
  connection_config: { ip_address: '192.168.2.50', access_code: 'xyz', serial_number: 'SN007' },
  awaiting_plate_clear: false,
  orca_printer_profiles: [],
  current_orca_printer_profile: null,
  enabled: true,
  queue_on: true,
  connected: true,
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', filament_profile: null, spoolman_spool_id: null }],
};

const INTEGRATION_TYPES: PrinterType[] = [
  {
    printer_type: 'bambu',
    display_name: 'Bambu Lab',
    connection_fields: [
      { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '192.168.1.x', help_text: '' },
    ],
  },
];

const INTEGRATION_SPOOL = {
  id: 7,
  remaining_weight: 800,
  used_weight: 0,
  filament: { id: 30, vendor: { name: 'Bambu' }, name: 'Basic White PLA', material: 'PLA', color_hex: 'FFFFFF' },
};

describe('PrintersScreen EditForm + SlotSpoolPicker integration', () => {
  let patchCalls: Array<[string, RequestInit]>;

  beforeEach(() => {
    patchCalls = [];
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        patchCalls.push([url, init as RequestInit]);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(INTEGRATION_PRINTER) });
      }
      if (url === '/api/v1/settings/spoolman') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: true, url: 'http://spoolman.local', api_key: null }) });
      }
      if (url === '/api/v1/spoolman/spools') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([INTEGRATION_SPOOL]) });
      }
      if (url === '/api/v1/spoolman/filaments') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.includes('orca-machine-catalog')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.match(/\/api\/v1\/printers\/\d+\/profiles/)) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ print_profiles: [], filament_profiles: [] }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('saves correct spool fields when a spool is selected in EditForm', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const onCancel = vi.fn();

    render(
      <MemoryRouter>
        <EditForm
          printer={INTEGRATION_PRINTER}
          types={INTEGRATION_TYPES}
          onSave={onSave}
          onCancel={onCancel}
        />
      </MemoryRouter>
    );

    // Wait for the spool combobox to appear (after useSpools fetches)
    const searchInput = await screen.findByPlaceholderText('Search spools…');

    // Focus to open dropdown, then pick the spool via mouseDown
    fireEvent.focus(searchInput);
    const spoolOption = await screen.findByText(/#7 Bambu Basic White PLA PLA/);
    fireEvent.mouseDown(spoolOption);

    // Save
    await user.click(screen.getByRole('button', { name: /save/i }));

    // Verify PATCH was called with the correct spool fields
    expect(patchCalls.length).toBeGreaterThan(0);
    const body = JSON.parse(patchCalls[0][1].body as string);
    const slot = body.loaded_filaments[0];
    expect(slot.spoolman_spool_id).toBe('7');
    expect(slot.type).toBe('PLA');
    expect(slot.color).toBe('#FFFFFF');
  });
});
