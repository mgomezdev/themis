import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { QueueError, QueueSuccess } from './QueueResult';

describe('QueueSuccess', () => {
  it.each([[1, '1 job added to queue'], [3, '3 jobs added to queue']])('counts %d job(s)', (n, text) => {
    render(<QueueSuccess jobCount={n} warnings={[]} onOpenQueue={() => {}} onOpenProject={() => {}} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('lists each group warning', () => {
    render(<QueueSuccess jobCount={2} warnings={['Fine: no printer', 'Draft: slow']} onOpenQueue={() => {}} onOpenProject={() => {}} />);
    expect(screen.getByText('Fine: no printer')).toBeInTheDocument();
    expect(screen.getByText('Draft: slow')).toBeInTheDocument();
  });

  it('opens the queue or the project', async () => {
    const onOpenQueue = vi.fn();
    const onOpenProject = vi.fn();
    render(<QueueSuccess jobCount={1} warnings={[]} onOpenQueue={onOpenQueue} onOpenProject={onOpenProject} />);
    await userEvent.click(screen.getByRole('button', { name: 'Queue' }));
    await userEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(onOpenQueue).toHaveBeenCalledTimes(1);
    expect(onOpenProject).toHaveBeenCalledTimes(1);
  });
});

describe('QueueError', () => {
  it('shows the message and retries unless busy', async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<QueueError message="Sidecar down" busy={false} onRetry={onRetry} />);
    expect(screen.getByText('Sidecar down')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<QueueError message="Sidecar down" busy onRetry={onRetry} />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDisabled();
  });
});
