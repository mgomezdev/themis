import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { JobDetailScreen } from './JobDetailScreen';
import * as queueApi from '../api/queue';

vi.mock('../api/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof queueApi>();
  return {
    ...actual,
    getJobDetails: vi.fn(),
    cancelJob: vi.fn(),
    unblockJob: vi.fn(),
  };
});

const BASE_JOB: queueApi.ApiJobDetails = {
  id: 5,
  uploaded_file_id: 10,
  plate_number: 1,
  order_id: null,
  assigned_printer_id: null,
  queue_position: 1,
  status: 'queued',
  overrides: null,
  block_reason: null,
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
  materials: [],
  eligible_printers: [],
  low_stock_warning: null,
  printer_configs: [
    {
      printer_id: 3,
      printer_name: 'U1',
      printer_type: 'snapmaker_extended',
      print_profile: '0.20mm Standard @U1',
      filament_profile: 'PLA @U1',
      filament_id: 12,
      filament_type: 'PLA',
      filament_color: '#000000',
      tool_index: 0,
      slice_failed: false,
      slice_error: null,
      low_stock_warning: null,
    },
  ],
};

function renderJobDetail(jobId = 5) {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${jobId}`]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('JobDetailScreen — low_stock_warning', () => {
  it('shows the low-stock warning on the printer config card when present', async () => {
    const job: queueApi.ApiJobDetails = {
      ...BASE_JOB,
      printer_configs: [
        {
          ...BASE_JOB.printer_configs[0],
          low_stock_warning: {
            spool_id: 12,
            spool_label: 'Black PLA #12',
            remaining_g: 220,
            needed_g: 340,
            message: 'project needs ~340g PLA, spool Black PLA #12 has ~220g remaining',
          },
        },
      ],
    };
    vi.mocked(queueApi.getJobDetails).mockResolvedValue(job);

    renderJobDetail();

    expect(await screen.findByText(/Low filament/i)).toBeTruthy();
    expect(
      await screen.findByText('project needs ~340g PLA, spool Black PLA #12 has ~220g remaining'),
    ).toBeTruthy();
  });

  it('does not show a low-stock warning when low_stock_warning is null', async () => {
    vi.mocked(queueApi.getJobDetails).mockResolvedValue(BASE_JOB);

    renderJobDetail();

    // Wait for the printer config card to render first
    await screen.findByText('U1');
    expect(screen.queryByText(/Low filament/i)).toBeNull();
  });
});
