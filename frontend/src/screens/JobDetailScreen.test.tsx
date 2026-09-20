import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    completeJobManually: vi.fn(),
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

describe('JobDetailScreen — manual completion', () => {
  it('shows the button for a queued job', async () => {
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    renderJobDetail();
    expect(await screen.findByRole('button', { name: /mark as already completed/i })).toBeTruthy();
  });

  it('does not show the button for a completed job', async () => {
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'complete' });
    renderJobDetail();
    await screen.findByText(/part\.3mf/);
    expect(screen.queryByRole('button', { name: /mark as already completed/i })).toBeNull();
  });

  it('requires confirmation before calling the API', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    expect(queueApi.completeJobManually).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /^confirm$/i })).toBeTruthy();
  });

  it('pre-selects the single eligible printer and calls the API on confirm', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    vi.mocked(queueApi.completeJobManually).mockResolvedValue({ ...BASE_JOB, status: 'complete' } as queueApi.ApiJob);
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    // completeJobManually is called, then handleCompleteManually awaits a
    // getJobDetails() refresh before the confirm panel closes — wait for that
    // full chain to settle rather than asserting immediately after the click.
    await waitFor(() => {
      expect(queueApi.completeJobManually).toHaveBeenCalledWith(5, 3); // job id 5, the sole printer_configs entry (printer_id 3)
    });
  });

  it('shows a printer picker when more than one config exists', async () => {
    const user = userEvent.setup();
    const job: queueApi.ApiJobDetails = {
      ...BASE_JOB,
      status: 'queued',
      printer_configs: [
        { ...BASE_JOB.printer_configs[0], printer_id: 3, printer_name: 'U1' },
        { ...BASE_JOB.printer_configs[0], printer_id: 4, printer_name: 'U2' },
      ],
    };
    vi.mocked(queueApi.getJobDetails).mockResolvedValue(job);
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));

    // "U1"/"U2" also appear in the pre-existing eligible-printers card list, so
    // scope the assertion to the select's own options rather than the whole page.
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map(o => o.textContent);
    expect(optionLabels).toContain('U1');
    expect(optionLabels).toContain('U2');
  });

  it('shows an error message when the API call fails', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.getJobDetails).mockResolvedValue({ ...BASE_JOB, status: 'queued' });
    vi.mocked(queueApi.completeJobManually).mockRejectedValue(new Error('500 slice error'));
    renderJobDetail();

    await user.click(await screen.findByRole('button', { name: /mark as already completed/i }));
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    expect(await screen.findByText(/500 slice error/i)).toBeTruthy();
  });
});
