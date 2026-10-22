import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueueScreen } from './QueueScreen';
import type { ApiJob } from '../api/queue';

// Mock the queue API module
vi.mock('../api/queue', () => ({
  useQueue: vi.fn(() => ({ jobs: [], refetch: vi.fn() })),
  useFilePlates: vi.fn(() => ({ getPlate: () => null, getFileName: () => null })),
  cancelJob: vi.fn(),
  unblockJob: vi.fn(),
  reorderJob: vi.fn(),
  getSliceFailures: vi.fn(() => Promise.resolve([])),
  getJobDetails: vi.fn(() => Promise.resolve({ printer_configs: [] })),
  verifySlice: vi.fn(),
  setJobProject: vi.fn(),
  plateThumbnailUrl: vi.fn(() => null),
}));

vi.mock('../api/projects', () => ({
  useProjects: vi.fn(() => ({ projects: [], refetch: vi.fn() })),
}));

vi.mock('../api/fleet', () => ({
  useFleetData: vi.fn(() => [[], vi.fn()]),
}));

import * as queueApi from '../api/queue';
import * as fleetApi from '../api/fleet';
import * as projectsApi from '../api/projects';

const nullEstimate = {
  actual_filament_grams: null, actual_seconds: null, actual_filament_breakdown: null,
  deduction_skipped: null, estimate_status: null, estimate_seconds: null,
  estimate_filament_grams: null, estimate_filament_breakdown: null, estimate_preset_label: null,
  materials: [] as string[], eligible_printers: [] as Array<{ id: number; name: string }>, model_targets: [] as ApiJob['model_targets'],
  low_stock_warning: null, filament_cost: null, not_before: null,
  save_slice: false, save_slice_name: null, allow_cached_slice: false, sliced_version_id: null, slice_cache_info: null,
};

const mockJobs: ApiJob[] = [
  {
    id: 1, uploaded_file_id: 10, plate_number: 1,
    order_id: null, assigned_printer_id: 2,
    queue_position: 1.0, status: 'printing', overrides: null, block_reason: null,
    created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z',
    ...nullEstimate,
  },
  {
    id: 2, uploaded_file_id: 10, plate_number: 2,
    order_id: null, assigned_printer_id: null,
    queue_position: 2.0, status: 'queued', overrides: null, block_reason: null,
    created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z',
    ...nullEstimate,
  },
];

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

describe('QueueScreen', () => {
  beforeEach(() => {
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: mockJobs, refetch: vi.fn() });
    vi.mocked(queueApi.useFilePlates).mockReturnValue({ getPlate: () => null, getFileName: () => null });
  });

  it('shows the linked project name on a job card', () => {
    vi.mocked(projectsApi.useProjects).mockReturnValue({ projects: [{ id: 4, name: 'Bracket run' }], refetch: vi.fn() } as never);
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [{ ...mockJobs[1], project_id: 4 }, mockJobs[0]], refetch: vi.fn() });
    render(<QueueScreen />, { wrapper });
    expect(screen.getAllByText('Bracket run')).toHaveLength(1);   // only the linked job's card
  });

  it('opens the job panel with a project control that refetches the queue after a change', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const refetchProjects = vi.fn();
    vi.mocked(projectsApi.useProjects).mockReturnValue({ projects: [{ id: 4, name: 'Bracket run' }], refetch: refetchProjects } as never);
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [{ ...mockJobs[1], project_id: 4 }], refetch });
    vi.mocked(queueApi.setJobProject).mockResolvedValue({ ...mockJobs[1], project_id: null });
    render(<QueueScreen />, { wrapper });
    await user.click(screen.getByText('Plate 2'));
    await user.click(screen.getByRole('button', { name: 'Unlink' }));
    await vi.waitFor(() => expect(refetch).toHaveBeenCalled());
    expect(refetchProjects).toHaveBeenCalled();
    expect(queueApi.setJobProject).toHaveBeenCalledWith(2, null);
  });

  it('renders summary stats', () => {
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText(/In progress/i)).toBeTruthy();
    expect(screen.getByText(/In queue/i)).toBeTruthy();
  });

  it('shows job cards for queued and printing jobs', () => {
    render(<QueueScreen />, { wrapper });
    // Should show two job cards (Plate 1 printing, Plate 2 queued)
    expect(screen.getAllByText(/Plate \d/i).length).toBeGreaterThan(0);
  });

  it('filter chips show only the matching jobs', async () => {
    const user = userEvent.setup();
    render(<QueueScreen />, { wrapper });
    expect(screen.getAllByText(/^Plate \d$/)).toHaveLength(2);                 // "All": the printing job and the queued one

    await user.click(screen.getByRole('button', { name: 'Queued' }));
    expect(screen.getAllByText(/^Plate \d$/).map(e => e.textContent)).toEqual(['Plate 2']);
    expect(screen.queryByText(/^Active$/)).toBeNull();                          // the Active chip steps aside

    await user.click(screen.getByRole('button', { name: /^Done/ }));
    expect(screen.queryAllByText(/^Plate \d$/)).toHaveLength(0);
    expect(screen.getByText(/Nothing here/i)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /^All/ }));
    expect(screen.getAllByText(/^Plate \d$/)).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Active' }));
    expect(screen.getAllByText(/^Plate \d$/).map(e => e.textContent)).toEqual(['Plate 1']);
  });

  it('marks jobs that save their gcode or print a cached, stale version (BIZ-194)', () => {
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [
      { ...mockJobs[1], save_slice: true },
      { ...mockJobs[1], id: 3, plate_number: 3, sliced_version_id: 4,
        slice_cache_info: { decision: 'hit', stale: true, stale_reasons: ['slicer_version_changed'] } },
    ], refetch: vi.fn() });
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText('Saving gcode')).toBeTruthy();
    expect(screen.getByText('Used cached gcode')).toBeTruthy();
    expect(screen.getByText('Stale: OrcaSlicer updated')).toBeTruthy();
  });

  it('renders empty state when no jobs', () => {
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [], refetch: vi.fn() });
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText(/Nothing here/i)).toBeTruthy();
  });

  it.each([
    ['Plate 1', 1],
    ['Plate 2', 2],
  ])('the remove button in the %s panel cancels job %i and only that job', async (plate, id) => {
    const user = userEvent.setup();
    vi.mocked(queueApi.cancelJob).mockClear();
    vi.mocked(queueApi.cancelJob).mockResolvedValue(mockJobs[id - 1]);
    render(<QueueScreen />, { wrapper });

    await user.click(screen.getByText(plate));                                   // open that job's detail panel
    await user.click(screen.getByRole('button', { name: /remove from queue/i }));

    expect(vi.mocked(queueApi.cancelJob)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(queueApi.cancelJob)).toHaveBeenCalledWith(id);
  });

  it('renders detailed error messages and category on failed job cards', () => {
    const failedJob: ApiJob = {
      id: 3,
      uploaded_file_id: 10,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 3.0,
      status: 'failed',
      overrides: null,
      block_reason: 'Gcode upload failed: [WinError 10054] Connection reset',
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [failedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    // Should render the categorized title "Upload Error"
    expect(screen.getByText('Upload Error')).toBeTruthy();
    // Should render the cleaned up error message
    expect(screen.getByText('[WinError 10054] Connection reset')).toBeTruthy();
  });

  it('renders blocked reason on blocked job cards', () => {
    const blockedJob: ApiJob = {
      id: 4,
      uploaded_file_id: 10,
      plate_number: 2,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 4.0,
      status: 'blocked',
      overrides: null,
      block_reason: 'filament mismatch: PLA color #ff0000 not found',
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [blockedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    // Should render the categorized title "Blocked / Waiting"
    expect(screen.getByText('Blocked / Waiting')).toBeTruthy();
    // Should render the blocked reason
    expect(screen.getByText('filament mismatch: PLA color #ff0000 not found')).toBeTruthy();
  });

  it('renders correct remaining print time for active jobs', () => {
    const activeJob: ApiJob = {
      id: 9,
      uploaded_file_id: 1,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: 1,
      queue_position: 1.0,
      status: 'printing',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [activeJob], refetch: vi.fn() });

    // Mock printer 1 with 61 minutes remaining
    const mockPrinter = {
      id: '1',
      name: 'Barnabus',
      nickname: 'Barnabus',
      model: 'elegoo_centauri',
      badge: 'ELE',
      buildVolume: '',
      capabilities: [],
      chamber: false,
      status: 'printing' as const,
      progress: 15,
      timeRemaining: 61,
      timeElapsed: 10,
      layer: { now: 3, total: 120 },
      nozzleTemp: 220,
      bedTemp: 55,
      chamberTemp: null,
      material: { name: 'PLA', type: 'PLA', color: '#000000' },
      currentJobId: 'plate_1.gcode',
      accent: '#888888',
      fanModel: 100,
      fanAux: 69,
      fanBox: 100,
      bedTempTarget: 55,
      queueOn: true,
      awaitingPlateClear: false,
      noSnapshotsWhileIdle: false,
    };
    vi.mocked(fleetApi.useFleetData).mockReturnValue([[mockPrinter], vi.fn()]);

    // Mock useFilePlates to return estimated_time = 0
    vi.mocked(queueApi.useFilePlates).mockReturnValue({
      getPlate: () => ({ plate_number: 1, thumbnail_path: null, estimated_time: 0, filament_g: 0 }),
      getFileName: () => null,
    });

    render(<QueueScreen />, { wrapper });

    // We expect the remaining time (61m = 1h 1m) to be rendered on the page
    expect(screen.getByText('1h 1m')).toBeTruthy();
  });

  it('shows filename on job card when available', async () => {
    const queuedJob: ApiJob = {
      id: 5,
      uploaded_file_id: 20,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'queued',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [queuedJob], refetch: vi.fn() });
    vi.mocked(queueApi.useFilePlates).mockReturnValue({
      getPlate: () => null,
      getFileName: () => 'my_model.3mf',
    });

    render(<QueueScreen />, { wrapper });

    expect(screen.getByText('my_model.3mf')).toBeTruthy();
  });

  it('blocked job appears in queued filter', async () => {
    const user = userEvent.setup();
    const blockedJob: ApiJob = {
      id: 6,
      uploaded_file_id: 10,
      plate_number: 3,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'blocked',
      overrides: null,
      block_reason: 'no matching filament',
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [blockedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    const queuedBtn = screen.getByRole('button', { name: /queued/i });
    await user.click(queuedBtn);

    // Plate 3 (blocked) should still be visible under "queued" filter
    expect(screen.getByText('Plate 3')).toBeTruthy();
  });

  it('failed chip appears when failed jobs exist', () => {
    const failedJob: ApiJob = {
      id: 7,
      uploaded_file_id: 10,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'failed',
      overrides: null,
      block_reason: 'slicing failed: some error',
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [failedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    expect(screen.getByRole('button', { name: /failed/i })).toBeTruthy();
  });

  it('reorder buttons appear for queued jobs in detail panel', async () => {
    const user = userEvent.setup();
    const queuedJob: ApiJob = {
      id: 8,
      uploaded_file_id: 10,
      plate_number: 2,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'queued',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [queuedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    // Click the job card to open the detail panel
    const cards = screen.getAllByText(/Plate \d/i);
    await user.click(cards[0]);

    // Reorder buttons should be visible
    expect(screen.getByRole('button', { name: /front/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /up/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /down/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /back/i })).toBeTruthy();
  });

  it('shows a countdown for a job scheduled for later, and nothing once it is due', () => {
    const base: ApiJob = {
      id: 11, uploaded_file_id: 10, plate_number: 1, order_id: null, assigned_printer_id: null, queue_position: 1.0,
      status: 'queued', overrides: null, block_reason: null,
      created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z', ...nullEstimate,
    };
    const inTwoHours = new Date(Date.now() + 2 * 3600_000 + 30_000).toISOString();
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [{ ...base, not_before: inTwoHours }], refetch: vi.fn() });
    const { unmount } = render(<QueueScreen />, { wrapper });
    expect(screen.getByText('in 2h 1m')).toBeTruthy();
    unmount();

    vi.mocked(queueApi.useQueue).mockReturnValue({
      jobs: [{ ...base, not_before: new Date(Date.now() - 60_000).toISOString() }], refetch: vi.fn() });
    render(<QueueScreen />, { wrapper });
    expect(screen.queryByText(/^in \d/)).toBeNull();
  });

  it('shows low-stock warning strip on job card when present', () => {
    const warnedJob: ApiJob = {
      id: 10,
      uploaded_file_id: 10,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'queued',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
      low_stock_warning: {
        spool_id: 12,
        spool_label: 'Black PLA #12',
        remaining_g: 220,
        needed_g: 340,
        message: 'project needs ~340g PLA, spool Black PLA #12 has ~220g remaining',
      },
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [warnedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    expect(screen.getByText(/Low filament/i)).toBeTruthy();
    expect(screen.getByText('project needs ~340g PLA, spool Black PLA #12 has ~220g remaining')).toBeTruthy();
  });

  it('does not show low-stock warning strip when low_stock_warning is null', () => {
    const okJob: ApiJob = {
      id: 11,
      uploaded_file_id: 10,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'queued',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [okJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    expect(screen.queryByText(/Low filament/i)).toBeNull();
  });

  it('shows low-stock warning in job detail panel', async () => {
    const user = userEvent.setup();
    const warnedJob: ApiJob = {
      id: 12,
      uploaded_file_id: 10,
      plate_number: 1,
      order_id: null,
      assigned_printer_id: null,
      queue_position: 1.0,
      status: 'queued',
      overrides: null,
      block_reason: null,
      created_at: '2026-05-27T00:00:00Z',
      updated_at: '2026-05-27T00:00:00Z',
      ...nullEstimate,
      low_stock_warning: {
        spool_id: 12,
        spool_label: 'Black PLA #12',
        remaining_g: 220,
        needed_g: 340,
        message: 'project needs ~340g PLA, spool Black PLA #12 has ~220g remaining',
      },
    };
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [warnedJob], refetch: vi.fn() });

    render(<QueueScreen />, { wrapper });

    const cards = screen.getAllByText(/Plate \d/i);
    await user.click(cards[0]);

    // Message renders in both the card strip and the detail panel
    expect(screen.getAllByText('project needs ~340g PLA, spool Black PLA #12 has ~220g remaining').length).toBeGreaterThan(1);
  });
});

