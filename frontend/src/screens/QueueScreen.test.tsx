import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueueScreen } from './QueueScreen';
import type { ApiJob } from '../api/queue';

// Mock the queue API module
vi.mock('../api/queue', () => ({
  useQueue: vi.fn(() => ({ jobs: [], refetch: vi.fn() })),
  useFilePlates: vi.fn(() => () => null),
  cancelJob: vi.fn(),
  plateThumbnailUrl: vi.fn(() => null),
}));

vi.mock('../api/fleet', () => ({
  useFleetData: vi.fn(() => [[], vi.fn()]),
}));

import * as queueApi from '../api/queue';
import * as fleetApi from '../api/fleet';

const mockJobs: ApiJob[] = [
  {
    id: 1, uploaded_file_id: 10, plate_number: 1,
    order_id: null, assigned_printer_id: 2,
    queue_position: 1.0, status: 'printing', overrides: null, block_reason: null,
    created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z',
  },
  {
    id: 2, uploaded_file_id: 10, plate_number: 2,
    order_id: null, assigned_printer_id: null,
    queue_position: 2.0, status: 'queued', overrides: null, block_reason: null,
    created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z',
  },
];

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

describe('QueueScreen', () => {
  beforeEach(() => {
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: mockJobs, refetch: vi.fn() });
    vi.mocked(queueApi.useFilePlates).mockReturnValue(() => null);
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

  it('filter chips are clickable', async () => {
    const user = userEvent.setup();
    render(<QueueScreen />, { wrapper });
    const queuedBtn = screen.getByRole('button', { name: /queued/i });
    await user.click(queuedBtn);
    // After clicking queued filter, "Active" chip is hidden
    expect(screen.queryByText(/^Active$/)).toBeNull();
  });

  it('renders empty state when no jobs', () => {
    vi.mocked(queueApi.useQueue).mockReturnValue({ jobs: [], refetch: vi.fn() });
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText(/Nothing here/i)).toBeTruthy();
  });

  it('cancel button calls cancelJob', async () => {
    const user = userEvent.setup();
    vi.mocked(queueApi.cancelJob).mockResolvedValue(mockJobs[1]);
    render(<QueueScreen />, { wrapper });

    // Click a job card to open detail panel
    const cards = screen.getAllByText(/Plate \d/i);
    await user.click(cards[0]);

    // Find and click remove button
    const removeBtn = screen.getByRole('button', { name: /remove from queue/i });
    await user.click(removeBtn);

    expect(vi.mocked(queueApi.cancelJob)).toHaveBeenCalled();
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
    };
    vi.mocked(fleetApi.useFleetData).mockReturnValue([[mockPrinter], vi.fn()]);

    // Mock useFilePlates to return estimated_time = 0
    vi.mocked(queueApi.useFilePlates).mockReturnValue(() => ({
      plate_number: 1,
      thumbnail_path: null,
      estimated_time: 0,
      filament_g: 0,
    }));

    render(<QueueScreen />, { wrapper });
    
    // We expect the remaining time (61m = 1h 1m) to be rendered on the page
    expect(screen.getByText('1h 1m')).toBeTruthy();
  });
});

