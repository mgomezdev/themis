import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';
import * as queueApi from '../api/queue';

// ── Inventory mock helpers ─────────────────────────────────────────────────────

const MOCK_FILAMENTS = [
  mkMaterial({ ref: '7', name: 'Sky Blue', vendor: 'ELEGOO', material: 'PLA', color_hex: '#5B9BD5' }),
  mkMaterial({ ref: '19', name: 'White', vendor: 'Sunlu', material: 'PLA+', color_hex: '#FFFFFF' }),
];

vi.mock('../api/inventory', async importOriginal => ({
  ...(await importOriginal<typeof import('../api/inventory')>()),
  useInventory: vi.fn(),
  useMaterials: vi.fn(),
}));

import * as inventoryApi from '../api/inventory';
import { activeInventory, mkMaterial, noInventory } from '../test/inventoryFixtures';

function mockSpoolmanConnected() {
  vi.mocked(inventoryApi.useInventory).mockReturnValue(activeInventory());
  vi.mocked(inventoryApi.useMaterials).mockReturnValue(MOCK_FILAMENTS);
}

function mockSpoolmanDisconnected() {
  vi.mocked(inventoryApi.useInventory).mockReturnValue(noInventory());
  vi.mocked(inventoryApi.useMaterials).mockReturnValue([]);
}

// ── API mocks ─────────────────────────────────────────────────────────────────

vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof queueApi>();
  return {
    ...actual,
    uploadFile: vi.fn(),
    createJob: vi.fn(),
    getPrinterProfiles: vi.fn().mockResolvedValue({
      print_profiles: ['0.20mm Standard @ECC'],
      filament_profiles: [],
    }),
  };
});

const MOCK_PRINTER = {
  id: 1,
  name: 'Barnabus',
  printer_type: 'elegoo_centauri',
  connection_config: {},
  awaiting_plate_clear: false,
  orca_printer_profiles: ['Elegoo Centauri Carbon'],
  current_orca_printer_profile: 'Elegoo Centauri Carbon',
  enabled: true,
  connected: true,
  loaded_filaments: [],
};

const MOCK_UPLOADED_FILE = {
  id: 42,
  original_filename: 'test.3mf',
  plates: [{ plate_number: 1, estimated_time: 300, filament_g: 10, thumbnail_path: null }],
};

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
class FakeWS { onmessage: ((e: MessageEvent) => void) | null = null; close() {} }
vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

// ── Setup and helpers ─────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockFetch.mockImplementation((url: string) => {
    let data: unknown;
    if (url === '/api/v1/printers') {
      data = [MOCK_PRINTER];
    } else if (url === `/api/v1/files/${MOCK_UPLOADED_FILE.id}/plates`) {
      data = { filename: MOCK_UPLOADED_FILE.original_filename, plates: MOCK_UPLOADED_FILE.plates };
    } else if (url.includes('/model-filaments')) {
      data = [];
    } else if (url.includes('/embedded-settings')) {
      data = [];
    } else if (url === '/api/v1/orders') {
      data = [];
    } else if (url.startsWith('/api/v1/files')) {
      data = [];
    } else {
      data = [];
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
  });
  vi.mocked(queueApi.uploadFile).mockResolvedValue(MOCK_UPLOADED_FILE as never);
  vi.mocked(queueApi.createJob).mockResolvedValue({ id: 1, status: 'queued' } as never);
  mockSpoolmanDisconnected();
});

/** Upload a file and wait for the plate + printer list to appear. */
async function uploadAndExpand(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(['dummy'], 'model.3mf', { type: 'application/octet-stream' });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(input, file);
  await waitFor(() => expect(screen.getByText('Barnabus')).toBeTruthy());
}

/** Select Barnabus as an eligible printer and wait for its config row to appear. */
async function selectPrinter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText('Barnabus'));
  await waitFor(() => expect(screen.getByText('Print profile')).toBeTruthy());
}

/** Pick the first print profile from the select. */
async function selectPrintProfile(user: ReturnType<typeof userEvent.setup>) {
  const profileSelect = screen.getByTestId('print-profile-select');
  await user.selectOptions(profileSelect, '0.20mm Standard @ECC');
}

/** Switch the filament mode to "type-color" so filament inputs (including color) are shown. */
async function switchToRequireFilament(user: ReturnType<typeof userEvent.setup>) {
  const modeSelect = await screen.findByTestId('filament-mode');
  await user.selectOptions(modeSelect, 'type-color');
}

// ── Basic rendering ───────────────────────────────────────────────────────────

describe('NewJobScreen — rendering', () => {
  it('renders dropzone', () => {
    mockSpoolmanDisconnected();
    render(<NewJobScreen />, { wrapper });
    expect(screen.getAllByText(/Drop a \.3mf, \.stl, \.gcode or \.gcode\.3mf file/i).length).toBeGreaterThan(0);
  });

  it('"Add jobs" button is disabled before a file is uploaded', () => {
    mockSpoolmanDisconnected();
    render(<NewJobScreen />, { wrapper });
    expect(screen.getByRole('button', { name: /add.*job/i })).toBeDisabled();
  });
});

// ── Filament UI — Spoolman disconnected ───────────────────────────────────────

describe('Filament input — Spoolman not configured', () => {
  beforeEach(() => mockSpoolmanDisconnected());

  it('shows filament-mode select (defer by default) when Spoolman is off; switching to type-color shows manual inputs', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);

    // Defer is the default — filament inputs hidden
    const modeSelect = await screen.findByTestId('filament-mode');
    expect((modeSelect as HTMLSelectElement).value).toBe('defer');
    expect(screen.queryByTestId('filament-type-input')).toBeNull();
    expect(screen.queryByTestId('filament-catalog-select')).toBeNull();

    // Switch to type-color — manual inputs (type + color) appear
    await user.selectOptions(modeSelect, 'type-color');
    expect(screen.getByTestId('filament-type-input')).toBeTruthy();
    expect(screen.getByTestId('filament-color-input')).toBeTruthy();
  });

  it('submits job with filament_type and filament_color, filament_id null', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);
    await switchToRequireFilament(user);

    await user.type(screen.getByTestId('filament-type-input'), 'PETG');

    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());

    const call = vi.mocked(queueApi.createJob).mock.calls[0][0];
    const cfg = call.printer_configs[0];
    expect(cfg.filament_type).toBe('PETG');
    expect(cfg.filament_id).toBeNull();
    expect(cfg.filament_color).toBeTruthy();
  });
});

// ── Filament UI — Spoolman connected ─────────────────────────────────────────

describe('Filament input — Spoolman connected', () => {
  beforeEach(() => mockSpoolmanConnected());

  it('shows catalog select with filament options when Spoolman is active (after switching to require)', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await switchToRequireFilament(user);

    const sel = screen.getByTestId('filament-catalog-select');
    expect(sel).toBeTruthy();
    const options = Array.from(sel.querySelectorAll('option')).map(o => o.textContent);
    expect(options).toContain('ELEGOO Sky Blue · PLA');
    expect(options).toContain('Sunlu White · PLA+');
  });

  it('catalog select includes "Enter manually…" option', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await switchToRequireFilament(user);

    const options = Array.from(
      screen.getByTestId('filament-catalog-select').querySelectorAll('option')
    ).map(o => o.textContent);
    expect(options).toContain('Enter manually…');
  });

  it('submits the picked material as a provider-namespaced ref, with its type and colour', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);
    await switchToRequireFilament(user);

    await user.selectOptions(
      screen.getByTestId('filament-catalog-select'),
      'Sunlu White'
    );

    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());

    const cfg = vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0];
    expect(cfg.material_provider).toBe('spoolman');
    expect(cfg.material_ref).toBe('19');
    expect(cfg.filament_id).toBeNull();                 // the legacy numeric id is the server's mirror, not the client's to send
    expect(cfg.filament_type).toBe('PLA+');
    expect(cfg.filament_color).toBe('#FFFFFF');
    expect(cfg.filament_profile).toBe('Sunlu White');
  });

  it('switching to manual clears the material pick', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);
    await switchToRequireFilament(user);

    // First pick a catalog item
    await user.selectOptions(screen.getByTestId('filament-catalog-select'), 'Sunlu White');
    // Then switch to manual
    await user.selectOptions(screen.getByTestId('filament-catalog-select'), '__manual__');

    await waitFor(() => expect(screen.getByTestId('filament-type-input')).toBeTruthy());
    expect(screen.queryByTestId('filament-catalog-select')).toBeNull();

    await user.type(screen.getByTestId('filament-type-input'), 'ABS');
    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());

    const cfg = vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0];
    expect(cfg.filament_id).toBeNull();
    expect(cfg.filament_type).toBe('ABS');
  });

  it('"↩ Catalog" button returns to catalog select and clears manual fields', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await switchToRequireFilament(user);

    await user.selectOptions(screen.getByTestId('filament-catalog-select'), '__manual__');
    await waitFor(() => expect(screen.getByTestId('filament-type-input')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /↩ Catalog/i }));
    await waitFor(() => expect(screen.getByTestId('filament-catalog-select')).toBeTruthy());
    expect(screen.queryByTestId('filament-type-input')).toBeNull();
  });
});

