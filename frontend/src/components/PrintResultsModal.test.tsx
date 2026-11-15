import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PrintResultsModal } from './PrintResultsModal';
import { Reply, stubFetch } from '../test/fetchStub';

const PARTS = [{ project_item_id: 50, file_name: 'Bracket.stl', quantity: 4 }, { project_item_id: 51, file_name: 'Lid.stl', quantity: 2 }];
const pending = (print_failed: boolean) => ({ job_id: 5, project_id: 7, test_print_waiver_id: null, print_failed, parts: PARTS });
const NOTICE = 'This print ended in a failure — adjust if some parts are usable.';

afterEach(() => vi.unstubAllGlobals());

describe('PrintResultsModal: failed prints', () => {
  it('a failed print opens with every part failed, shows the notice and submits those counts', async () => {
    const api = stubFetch({ 'POST /api/v1/printers/1/plate-cleared': { ok: true } });
    render(<PrintResultsModal printerId="1" pending={pending(true)} onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByText(NOTICE)).toBeTruthy();
    expect(screen.getByTestId('succeeded-Bracket.stl').textContent).toBe('0');
    expect((screen.getByLabelText('Failed Lid.stl') as HTMLInputElement).value).toBe('2');
    fireEvent.click(screen.getByRole('button', { name: 'Clear plate' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/printers/1/plate-cleared')[0]?.body).toEqual({
      job_id: 5, results: [{ project_item_id: 50, quantity_failed: 4 }, { project_item_id: 51, quantity_failed: 2 }] }));
  });

  it('a completed print opens at zero failed with no notice', () => {
    render(<PrintResultsModal printerId="1" pending={pending(false)} onClose={() => {}} onDone={() => {}} />);
    expect(screen.queryByText(NOTICE)).toBeNull();
    expect(screen.getByTestId('succeeded-Bracket.stl').textContent).toBe('4');
    expect((screen.getByLabelText('Failed Lid.stl') as HTMLInputElement).value).toBe('0');
  });
});

describe('PrintResultsModal: late report mode', () => {
  it('saves through onSubmit (not the printer) with a "Save results" button', async () => {
    const api = stubFetch({});
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onDone = vi.fn();
    render(<PrintResultsModal pending={pending(false)} onClose={() => {}} onDone={onDone} onSubmit={onSubmit} />);
    expect(screen.queryByRole('button', { name: 'Clear plate' })).toBeNull();
    expect(screen.getByText('What came off this plate? Failed parts go back into the queue on their own.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Failed Bracket.stl'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenCalledWith([{ project_item_id: 50, quantity_failed: 1 }]);
    expect(api.calls).toEqual([]);
  });

  it('shows the server message and stays open when the report is refused', async () => {
    const onDone = vi.fn();
    const onSubmit = vi.fn().mockRejectedValue(new Error('Plate already reported'));
    render(<PrintResultsModal pending={pending(false)} onClose={() => {}} onDone={onDone} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await screen.findByText('Plate already reported');
    expect(onDone).not.toHaveBeenCalled();
  });
});

describe('reportJobResults (via History wiring)', () => {
  it('posts to /jobs/{id}/results and surfaces a 409 detail', async () => {
    const { reportJobResults } = await import('../api/queue');
    const api = stubFetch({ 'POST /api/v1/jobs/9/results': new Reply(409, { detail: 'Its parts are already being reprinted' }) });
    await expect(reportJobResults(9, [{ project_item_id: 1, quantity_failed: 2 }])).rejects.toThrow('Its parts are already being reprinted');
    expect(api.to('POST', '/api/v1/jobs/9/results')[0].body).toEqual({ results: [{ project_item_id: 1, quantity_failed: 2 }] });
  });
});
