import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { EditJobScreen } from './EditJobScreen';
import * as queueApi from '../api/queue';

// ── Spoolman mock ─────────────────────────────────────────────────────────────

vi.mock('../api/spoolman', () => ({
  useSpoolmanConfig: vi.fn(),
  useFilaments: vi.fn(),
  filamentDisplayName: vi.fn((f: { vendor?: { name: string }; name: string }) =>
    f.vendor ? `${f.vendor.name} ${f.name}` : f.name),
}));

import * as spoolmanApi from '../api/spoolman';

function mockSpoolmanDisconnected() {
  vi.mocked(spoolmanApi.useSpoolmanConfig).mockReturnValue({ config: null, refetch: vi.fn() });
  vi.mocked(spoolmanApi.useFilaments).mockReturnValue([]);
}

// ── Queue API mock ────────────────────────────────────────────────────────────

vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof queueApi>();
  return {
    ...actual,
    getJobDetails: vi.fn(),
    updateJobConfigs: vi.fn(),
    getPrinterProfiles: vi.fn().mockResolvedValue({
      print_profiles: ['0.20mm Standard @U1'],
      filament_profiles: [],
    }),
  };
});

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MULTI_PRINTER = {
  id: 3,
  name: 'U1',
  printer_type: 'snapmaker_extended',
  connection_config: {},
  awaiting_plate_clear: false,
  orca_printer_profiles: ['U1 Profile'],
  current_orca_printer_profile: 'U1 Profile',
  enabled: true,
  connected: true,
  loaded_filaments: [
    { slot: 0, type: 'PLA',  color: '#ffffff', name: 'PLA White',  filament_profile: 'PLA @U1' },
    { slot: 1, type: 'PETG', color: '#000000', name: 'PETG Black', filament_profile: 'PETG @U1' },
    { slot: 2, type: 'TPU',  color: '#00ff00', name: 'TPU Green',  filament_profile: 'TPU @U1' },
  ],
};

const JOB_WITH_TOOL2: queueApi.ApiJobDetails = {
  id: 5,
  uploaded_file_id: 10,
  plate_number: 1,
  order_id: null,
  assigned_printer_id: null,
  queue_position: 1,
  status: 'blocked',
  overrides: null,
  block_reason: 'slice failed',
  created_at: '2026-06-08T00:00:00Z',
  updated_at: '2026-06-08T00:00:00Z',
  file: { id: 10, original_filename: 'part.3mf' },
  plate: { estimated_time: 600, filament_g: 15, thumbnail_path: null },
  assigned_printer: null,
  filament_grams_live: null,
  estimated_seconds_live: null,
  actual_filament_grams: null,
  actual_seconds: null,
  actual_filament_breakdown: null,
  deduction_skipped: null,
  estimate_status: null,
  estimate_seconds: null,
  estimate_filament_grams: null,
  estimate_filament_breakdown: null,
  estimate_preset_label: null,
  printer_configs: [
    {
      printer_id: 3,
      printer_name: 'U1',
      printer_type: 'snapmaker_extended',
      print_profile: '0.20mm Standard @U1',
      filament_profile: 'TPU @U1',
      filament_id: null,
      filament_type: 'TPU',
      filament_color: '#00ff00',
      tool_index: 2,
      slice_failed: true,
      slice_error: 'profile mismatch',
    },
  ],
};

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
class FakeWS { onmessage: ((e: MessageEvent) => void) | null = null; close() {} }
vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);

// ── Render helper ─────────────────────────────────────────────────────────────

function renderEditJob(jobId = 5) {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${jobId}/edit`]}>
      <Routes>
        <Route path="/jobs/:id/edit" element={<EditJobScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockSpoolmanDisconnected();
  // Printers endpoint
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve([MULTI_PRINTER]),
  });
  vi.mocked(queueApi.getJobDetails).mockResolvedValue(JOB_WITH_TOOL2);
  vi.mocked(queueApi.updateJobConfigs).mockResolvedValue({ id: 5, status: 'queued' } as never);
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('EditJobScreen — tool_index round-trip', () => {
  it('pre-fills tool-select with the stored tool_index (2) from job printer_configs', async () => {
    renderEditJob();
    const sel = await screen.findByTestId('tool-select');
    expect((sel as HTMLSelectElement).value).toBe('2');
  });

  it('save payload includes tool_index matching the pre-filled selection', async () => {
    const user = userEvent.setup();
    renderEditJob();

    // Wait for the screen to load and the Save button to become enabled
    await screen.findByTestId('tool-select');
    const saveBtn = await screen.findByRole('button', { name: /Save & re-queue/i });

    // Print profile is already pre-filled (from the fixture), so isComplete should be true
    await waitFor(() => expect(saveBtn).not.toBeDisabled());

    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(queueApi.updateJobConfigs)).toHaveBeenCalled());

    const [, configs] = vi.mocked(queueApi.updateJobConfigs).mock.calls[0];
    const cfg = configs[0];
    expect(cfg.tool_index).toBe(2);
    expect(cfg.printer_id).toBe(3);
    expect(cfg.print_profile).toBe('0.20mm Standard @U1');
  });

  it('changing tool selection updates the payload tool_index', async () => {
    const user = userEvent.setup();
    renderEditJob();

    const sel = await screen.findByTestId('tool-select');
    // Change to tool 0
    await user.selectOptions(sel, '0');
    expect((sel as HTMLSelectElement).value).toBe('0');

    const saveBtn = await screen.findByRole('button', { name: /Save & re-queue/i });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(queueApi.updateJobConfigs)).toHaveBeenCalled());
    const [, configs] = vi.mocked(queueApi.updateJobConfigs).mock.calls[0];
    expect(configs[0].tool_index).toBe(0);
  });

  it('selecting Any/default tool sends tool_index: null', async () => {
    const user = userEvent.setup();
    renderEditJob();

    const sel = await screen.findByTestId('tool-select');
    // Select "Any / default tool" (empty value)
    await user.selectOptions(sel, '');
    expect((sel as HTMLSelectElement).value).toBe('');

    const saveBtn = await screen.findByRole('button', { name: /Save & re-queue/i });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(queueApi.updateJobConfigs)).toHaveBeenCalled());
    const [, configs] = vi.mocked(queueApi.updateJobConfigs).mock.calls[0];
    expect(configs[0].tool_index).toBeNull();
  });
});

describe('EditJobScreen — isComplete relaxed (defer)', () => {
  it('Save button is enabled when only printProfile is set (no filament)', async () => {
    // Job has a print_profile but tool = null and no filament ask
    const jobDeferFilament: queueApi.ApiJobDetails = {
      ...JOB_WITH_TOOL2,
      printer_configs: [{
        ...JOB_WITH_TOOL2.printer_configs[0],
        filament_profile: null,
        filament_type: null,
        filament_color: null,
        tool_index: null,
      }],
    };
    vi.mocked(queueApi.getJobDetails).mockResolvedValue(jobDeferFilament);

    renderEditJob();
    const saveBtn = await screen.findByRole('button', { name: /Save & re-queue/i });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
  });
});
