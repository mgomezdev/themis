import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SearchModal } from './SearchModal';
import type { ApiJob } from '../api/queue';
import type { Printer } from '../data/types';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

const JOBS: ApiJob[] = [
  {
    id: 101, uploaded_file_id: 1, plate_number: 1, order_id: 5, assigned_printer_id: null,
    queue_position: 1, status: 'queued', overrides: null, block_reason: null,
    actual_filament_grams: null, actual_seconds: null, actual_filament_breakdown: null, deduction_skipped: null,
    estimate_status: null, estimate_seconds: null, estimate_filament_grams: null, estimate_filament_breakdown: null,
    estimate_preset_label: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    materials: [], eligible_printers: [], low_stock_warning: null,
  },
];

const PRINTERS: Printer[] = [
  {
    id: 'p1', name: 'bambu_p1s', nickname: 'Sir Reginald', model: 'P1S', badge: 'P1S',
    buildVolume: '256x256x256', capabilities: [], chamber: false, status: 'idle',
    progress: 0, timeRemaining: 0, timeElapsed: 0, layer: null, nozzleTemp: 0, bedTemp: 0,
  } as unknown as Printer,
];

beforeEach(() => {
  navigateMock.mockClear();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/api/v1/orders')) {
      return new Response(JSON.stringify([
        { id: 5, order_type: 'standard', customer: 'Acme Robotics', title: 'Widget batch', due_date: null, notes: null, on_hold: false, parts: [], status: 'active', progress: 0, job_count: 1, created_at: '', updated_at: '' },
      ]), { status: 200 });
    }
    if (url.includes('/api/v1/files')) {
      return new Response(JSON.stringify([
        { id: 1, original_filename: 'bracket_v2.3mf', relative_path: 'bracket_v2.3mf', folder: '/', size_bytes: 100, plate_count: 1, uploaded_at: '', missing: false, tags: [], thumbnail_url: null, plate_thumbnails: [] },
      ]), { status: 200 });
    }
    return new Response('[]', { status: 200 });
  }));
});

function renderModal(open = true) {
  const onClose = vi.fn();
  const utils = render(
    <MemoryRouter>
      <SearchModal open={open} onClose={onClose} jobs={JOBS} printers={PRINTERS} />
    </MemoryRouter>
  );
  return { onClose, ...utils };
}

describe('SearchModal', () => {
  it('renders nothing when closed', () => {
    renderModal(false);
    expect(screen.queryByPlaceholderText(/search jobs/i)).toBeNull();
  });

  it('opens with an autofocused input', () => {
    renderModal(true);
    const input = screen.getByPlaceholderText(/search jobs/i);
    expect(input).toBeTruthy();
    expect(document.activeElement).toBe(input);
  });

  it('shows no results for an empty query', () => {
    renderModal(true);
    expect(screen.queryByText('No results')).toBeNull();
    expect(screen.queryByText('Jobs')).toBeNull();
  });

  it('matches a printer by nickname', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Reginald');
    await waitFor(() => expect(screen.getByText('Printers')).toBeInTheDocument());
    expect(screen.getByText('Sir Reginald')).toBeTruthy();
  });

  it('matches a file by original_filename, lazily fetched on open', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'bracket');
    // "bracket_v2.3mf" appears twice: once as the Job result (resolved via
    // uploaded_file_id) and once as the Files result itself.
    await waitFor(() => expect(screen.getAllByText('bracket_v2.3mf').length).toBe(2));
    expect(screen.getByText('Files')).toBeTruthy();
  });

  it('matches an order by customer name', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Acme');
    await waitFor(() => expect(screen.getByText('Widget batch')).toBeInTheDocument());
  });

  it('matches a job via its resolved file name', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'bracket');
    await waitFor(() => expect(screen.getByText('Jobs')).toBeInTheDocument());
    // Job label resolves to the associated file's name, not a bare "Job #101"
    const jobEntries = screen.getAllByText('bracket_v2.3mf');
    expect(jobEntries.length).toBeGreaterThanOrEqual(1);
  });

  it('shows "No results" when nothing matches', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'zzz_nonexistent');
    await waitFor(() => expect(screen.getByText('No results')).toBeInTheDocument());
  });

  it('navigates to /fleet and closes on selecting a printer result', async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Reginald');
    await waitFor(() => expect(screen.getByText('Sir Reginald')).toBeInTheDocument());
    await user.click(screen.getByText('Sir Reginald'));
    expect(navigateMock).toHaveBeenCalledWith('/fleet');
    expect(onClose).toHaveBeenCalled();
  });

  it('navigates to /jobs/{id} on selecting a job result', async () => {
    const user = userEvent.setup();
    renderModal(true);
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'bracket');
    await waitFor(() => expect(screen.getByText('Jobs')).toBeInTheDocument());
    const jobRow = screen.getAllByText('bracket_v2.3mf')[0];
    await user.click(jobRow);
    expect(navigateMock).toHaveBeenCalledWith('/jobs/101');
  });

  it('pressing Enter selects the first result', async () => {
    const user = userEvent.setup();
    renderModal(true);
    const input = screen.getByPlaceholderText(/search jobs/i);
    await user.type(input, 'Reginald');
    await waitFor(() => expect(screen.getByText('Sir Reginald')).toBeInTheDocument());
    await user.keyboard('{Enter}');
    expect(navigateMock).toHaveBeenCalledWith('/fleet');
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal(true);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('resets the query each time it is reopened', async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <MemoryRouter>
        <SearchModal open={true} onClose={vi.fn()} jobs={JOBS} printers={PRINTERS} />
      </MemoryRouter>
    );
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Reginald');
    expect((screen.getByPlaceholderText(/search jobs/i) as HTMLInputElement).value).toBe('Reginald');

    rerender(
      <MemoryRouter>
        <SearchModal open={false} onClose={vi.fn()} jobs={JOBS} printers={PRINTERS} />
      </MemoryRouter>
    );
    rerender(
      <MemoryRouter>
        <SearchModal open={true} onClose={vi.fn()} jobs={JOBS} printers={PRINTERS} />
      </MemoryRouter>
    );
    expect((screen.getByPlaceholderText(/search jobs/i) as HTMLInputElement).value).toBe('');
  });
});
