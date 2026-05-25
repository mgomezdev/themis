import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueueScreen } from './QueueScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

describe('QueueScreen', () => {
  it('renders summary stats', () => {
    render(<QueueScreen />, { wrapper });
    expect(screen.getByText(/Active/i)).toBeTruthy();
    expect(screen.getByText(/Queued/i)).toBeTruthy();
  });
  it('shows job cards', () => {
    render(<QueueScreen />, { wrapper });
    // Printing jobs appear
    expect(screen.getAllByText(/Printing/i).length).toBeGreaterThan(0);
  });
  it('filter chips are clickable', async () => {
    const user = userEvent.setup();
    render(<QueueScreen />, { wrapper });
    const queuedBtn = screen.getByRole('button', { name: /queued/i });
    await user.click(queuedBtn);
    // After clicking queued filter, only queued jobs shown (no "Active" stats section)
    expect(screen.queryByText(/^Active$/)).toBeNull();
  });
});
