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

import * as queueApi from '../api/queue';

const mockJobs: ApiJob[] = [
  {
    id: 1, uploaded_file_id: 10, plate_number: 1,
    project_id: null, assigned_printer_id: 2,
    queue_position: 1.0, status: 'printing',
    created_at: '2026-05-27T00:00:00Z', updated_at: '2026-05-27T00:00:00Z',
  },
  {
    id: 2, uploaded_file_id: 10, plate_number: 2,
    project_id: null, assigned_printer_id: null,
    queue_position: 2.0, status: 'queued',
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
});
