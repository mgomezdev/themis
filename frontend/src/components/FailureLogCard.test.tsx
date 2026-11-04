import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { FailureLogCard } from './FailureLogCard';
import { PrintResultsModal } from './PrintResultsModal';
import { stubFetch } from '../test/fetchStub';
import type { ProjectItem } from '../api/projects';

const ITEM = { id: 50, project_id: 7, file_id: 1, file_name: 'Bracket.stl', quantity: 4, quantity_completed: 2, quantity_failed: 1,
  filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null, sort_order: 0, group_id: 1,
  quantity_held: 1 } as ProjectItem;
const LOG = [
  { id: 1, project_item_id: 50, file_name: 'Bracket.stl', job_id: 5, printer_id: 3, printer_name: 'Kepler', quantity_failed: 1,
    created_at: '2026-11-03T10:00:00+00:00', resolution: 'requeued' },
  { id: 2, project_item_id: 50, file_name: 'Bracket.stl', job_id: 9, printer_id: 4, printer_name: 'Juno', quantity_failed: 1,
    created_at: '2026-11-03T12:00:00+00:00', resolution: 'held' },
];

describe('FailureLogCard (BIZ-364)', () => {
  it('lists each failure with its printer and status, and offers Reprint / Drop for held parts', async () => {
    const onChanged = vi.fn();
    const api = stubFetch({
      'GET /api/v1/projects/7/failures': LOG,
      'POST /api/v1/projects/7/items/50/held': { requeued_job_ids: [12] },
    });
    render(<FailureLogCard projectId={7} items={[ITEM]} onChanged={onChanged} />);
    expect(await screen.findByText('Kepler')).toBeTruthy();
    expect(screen.getByText('Juno')).toBeTruthy();
    expect(screen.getByText('Back in the queue')).toBeTruthy();
    expect(screen.getAllByText('Held').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Reprint' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/projects/7/items/50/held')[0]?.body).toEqual({ action: 'reprint' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('shows nothing for a project with no failures or held parts', async () => {
    stubFetch({ 'GET /api/v1/projects/7/failures': [] });
    const { container } = render(<FailureLogCard projectId={7} items={[{ ...ITEM, quantity_held: 0 }]} />);
    await waitFor(() => expect(container.textContent).toBe(''));
  });
});

describe('PrintResultsModal (BIZ-364)', () => {
  const pending = { job_id: 5, project_id: 7, parts: [
    { project_item_id: 50, file_name: 'Bracket.stl', quantity: 4 }, { project_item_id: 51, file_name: 'Lid.stl', quantity: 2 }] };

  it('"Whole plate failed" fails every row and "Nothing failed" resets them; only failures are sent', async () => {
    const api = stubFetch({ 'POST /api/v1/printers/1/plate-cleared': { ok: true } });
    const onDone = vi.fn();
    render(<PrintResultsModal printerId="1" pending={pending} onClose={() => {}} onDone={onDone} />);
    fireEvent.click(screen.getByRole('button', { name: 'Whole plate failed' }));
    expect(screen.getByTestId('succeeded-Bracket.stl').textContent).toBe('0');
    expect(screen.getByTestId('succeeded-Lid.stl').textContent).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: 'Nothing failed' }));
    expect(screen.getByTestId('succeeded-Bracket.stl').textContent).toBe('4');
    fireEvent.change(screen.getByLabelText('Failed Lid.stl'), { target: { value: '9' } });       // capped at what was on the plate
    expect(screen.getByTestId('succeeded-Lid.stl').textContent).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: 'Clear plate' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/printers/1/plate-cleared')[0]?.body).toEqual(
      { job_id: 5, results: [{ project_item_id: 51, quantity_failed: 2 }] }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });
});
