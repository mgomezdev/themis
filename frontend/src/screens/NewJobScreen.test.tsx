import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';
import * as queueApi from '../api/queue';

// ── Inventory mock helpers ─────────────────────────────────────────────────────

const MOCK_FILAMENTS = [
  mkMaterial({ ref: '7', name: 'Sky Blue', vendor: 'ELEGOO', material: 'PLA', color_hex: '#5B9BD5' }),
  mkMaterial({ ref: '19', name: 'White', vendor: 'Sunlu', material: 'PLA', subtype: 'plus', color_hex: '#FFFFFF' }),
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
  machine_profile: 'Elegoo Centauri Carbon',
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

// ── Filament ask (manufacturer / type / subtype / color) ─────────────────────────────────────

const ASK_NONE = { filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null };

describe('Filament ask — no inventory', () => {
  beforeEach(() => mockSpoolmanDisconnected());

  it('starts undefined ("any loaded filament"): the editor is there, no catalog is offered, and the job is created with a null ask', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);

    expect(await screen.findByTestId('filament-ask-type')).toBeTruthy();
    expect(screen.queryByTestId('filament-ask-pick')).toBeNull();
    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());
    expect(vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0]).toMatchObject(ASK_NONE);
  });

  it('submits a hand-typed tuple: a type gets subtype "any" until changed, and no material reference is sent', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);

    await user.type(await screen.findByTestId('filament-ask-manufacturer'), 'Acme');
    await user.selectOptions(screen.getByTestId('filament-ask-type'), 'PETG');
    await user.selectOptions(screen.getByTestId('filament-ask-subtype'), 'silk');
    await user.click(screen.getByTestId('filament-ask-color'));

    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());

    const cfg = vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0];
    expect(cfg).toMatchObject({ filament_manufacturer: 'Acme', filament_type: 'PETG', filament_subtype: 'silk', filament_color: '#1a73e8' });
    expect(Object.keys(cfg).filter(k => /material|filament_id/.test(k))).toEqual([]);
  });
});

describe('Filament ask — with an inventory', () => {
  beforeEach(() => mockSpoolmanConnected());

  it('offers "pick…" with the inventory\'s materials', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);

    await user.click(await screen.findByTestId('filament-ask-pick'));
    const options = Array.from(screen.getByTestId('filament-ask-material').querySelectorAll('option')).map(o => o.textContent);
    expect(options).toContain('ELEGOO Sky Blue (PLA)');
    expect(options).toContain('Sunlu White (PLA plus)');
  });

  it('picking a material fills the tuple only: manufacturer, type, subtype and colour, with no reference to the material', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);

    await user.click(await screen.findByTestId('filament-ask-pick'));
    await user.selectOptions(screen.getByTestId('filament-ask-material'), '19');
    expect((screen.getByTestId('filament-ask-type') as HTMLSelectElement).value).toBe('PLA');       // the fields show what was filled
    expect((screen.getByTestId('filament-ask-subtype') as HTMLSelectElement).value).toBe('plus');

    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());

    const cfg = vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0];
    expect(cfg).toMatchObject({ filament_manufacturer: 'Sunlu', filament_type: 'PLA', filament_subtype: 'plus', filament_color: '#FFFFFF' });
    expect(Object.keys(cfg).filter(k => /material|filament_id/.test(k))).toEqual([]);
  });

  it('the picked tuple can be edited afterwards (it is just four fields)', async () => {
    const user = userEvent.setup();
    render(<NewJobScreen />, { wrapper });
    await uploadAndExpand(user);
    await selectPrinter(user);
    await selectPrintProfile(user);

    await user.click(await screen.findByTestId('filament-ask-pick'));
    await user.selectOptions(screen.getByTestId('filament-ask-material'), '19');
    await user.selectOptions(screen.getByTestId('filament-ask-subtype'), 'any');
    await user.click(screen.getByRole('button', { name: /add.*job/i }));
    await waitFor(() => expect(vi.mocked(queueApi.createJob)).toHaveBeenCalled());
    expect(vi.mocked(queueApi.createJob).mock.calls[0][0].printer_configs[0]).toMatchObject({ filament_type: 'PLA', filament_subtype: 'any' });
  });
});
