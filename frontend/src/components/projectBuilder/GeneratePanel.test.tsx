import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GenerateError, GeneratePicker, GenerateSuccess } from './GeneratePanel';
import { stubFetch } from '../../test/fetchStub';

afterEach(() => vi.unstubAllGlobals());

function picker(ids: number[], disabled = false) {
  stubFetch({ 'GET /api/v1/printers': [], 'GET /api/v1/printers/1/profiles': { print_profiles: [], filament_profiles: [] } });
  const props = { onCancel: vi.fn(), onGenerate: vi.fn(), setEligiblePrinterIds: vi.fn(), setProcessPreset: vi.fn() };
  render(<GeneratePicker eligiblePrinterIds={ids} processPreset={null} disabled={disabled} {...props} />);
  return props;
}

describe('GeneratePicker', () => {
  it('creates without dispatch when no printer is chosen', async () => {
    const p = picker([]);
    const btn = screen.getByRole('button', { name: 'Create without dispatch' });
    expect(btn).toHaveClass('btn', 'sm');
    expect(btn).not.toHaveClass('primary');
    expect(btn).toHaveAttribute('title', 'No printers selected — jobs will be created but not dispatched');
    await userEvent.click(btn);
    expect(p.onGenerate).toHaveBeenCalledWith([]);
  });

  it('creates for the chosen printers', async () => {
    const p = picker([1]);
    const btn = screen.getByRole('button', { name: 'Create' });
    expect(btn).toHaveClass('primary');
    expect(btn).not.toHaveAttribute('title');
    await userEvent.click(btn);
    expect(p.onGenerate).toHaveBeenCalledWith([1]);
  });

  it('cancels, and can be disabled', async () => {
    const p = picker([1], true);
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(p.onCancel).toHaveBeenCalled();
  });
});

describe('GenerateSuccess', () => {
  it.each([[1, '1 job added to queue'], [3, '3 jobs added to queue']])('counts %d job(s)', (n, text) => {
    render(<GenerateSuccess jobCount={n} onOpenQueue={() => {}} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('opens the queue, and the project only when there is one', async () => {
    const onOpenQueue = vi.fn();
    const onOpenProject = vi.fn();
    const { rerender } = render(<GenerateSuccess jobCount={1} onOpenQueue={onOpenQueue} />);
    expect(screen.queryByRole('button', { name: 'Details' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Queue' }));
    rerender(<GenerateSuccess jobCount={1} onOpenQueue={onOpenQueue} onOpenProject={onOpenProject} />);
    await userEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(onOpenQueue).toHaveBeenCalledTimes(1);
    expect(onOpenProject).toHaveBeenCalledTimes(1);
  });
});

describe('GenerateError', () => {
  it('shows the message and retries unless busy', async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<GenerateError message="Sidecar down" busy={false} onRetry={onRetry} />);
    expect(screen.getByText('Sidecar down')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<GenerateError message="Sidecar down" busy onRetry={onRetry} />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDisabled();
  });
});
