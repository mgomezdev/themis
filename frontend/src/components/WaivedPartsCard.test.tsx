import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { WaivedPartsCard } from './WaivedPartsCard';
import { PrintResultsModal } from './PrintResultsModal';
import { Reply, stubFetch } from '../test/fetchStub';

const WAIVERS = [
  { id: 4, part_key: 'h:stl:', file_name: 'Bracket.stl', failures: 2, last_failure_at: '2026-11-03T10:00:00+00:00', projects: ['Kit A', 'Kit B'] },
  { id: 5, part_key: 'g:stl:', file_name: 'Lid.stl', failures: 3, last_failure_at: null, projects: [] },
];

describe('WaivedPartsCard (BIZ-365)', () => {
  it('lists the waived parts and offers Test print, Clear and Clear all', async () => {
    const api = stubFetch({
      'GET /api/v1/printers/7/waivers': WAIVERS,
      'POST /api/v1/printers/7/waivers/4/test-print': { job_id: 99 },
      'DELETE /api/v1/printers/7/waivers/5': new Reply(204),
      'DELETE /api/v1/printers/7/waivers': new Reply(204),
    });
    render(<WaivedPartsCard printerId={7} flagAt={0} stopAt={0} onThresholds={() => {}} />);
    expect(await screen.findByText(/Bracket\.stl/)).toBeTruthy();
    expect(screen.getByText(/failed 2× here/).textContent).toContain('Kit A, Kit B');

    fireEvent.click(screen.getAllByRole('button', { name: 'Test print' })[0]);
    await waitFor(() => expect(api.to('POST', '/api/v1/printers/7/waivers/4/test-print')).toHaveLength(1));
    expect(await screen.findByText(/next print/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear' })[1]);
    await waitFor(() => expect(api.to('DELETE', '/api/v1/printers/7/waivers/5')).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    await waitFor(() => expect(api.to('DELETE', '/api/v1/printers/7/waivers')).toHaveLength(1));
  });

  it('edits the flag / stop thresholds (whole numbers, 0 = off)', async () => {
    stubFetch({ 'GET /api/v1/printers/7/waivers': [] });
    const onThresholds = vi.fn();
    render(<WaivedPartsCard printerId={7} flagAt={0} stopAt={2} onThresholds={onThresholds} />);
    expect(await screen.findByText(/takes every part/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Flag at waived parts'), { target: { value: '3.6' } });
    expect(onThresholds).toHaveBeenLastCalledWith({ waiver_flag_at: 3, waiver_stop_at: 2 });
    fireEvent.change(screen.getByLabelText('Stop at waived parts'), { target: { value: '-1' } });
    expect(onThresholds).toHaveBeenLastCalledWith({ waiver_flag_at: 0, waiver_stop_at: 0 });
  });
});

describe('PrintResultsModal on a test print (BIZ-365)', () => {
  const pending = { job_id: 9, project_id: 7, test_print_waiver_id: 4, parts: [{ project_item_id: 50, file_name: 'Bracket.stl', quantity: 1 }] };

  it('clears the waiver when nothing failed, and not when the test failed', async () => {
    const api = stubFetch({ 'POST /api/v1/printers/1/plate-cleared': { ok: true } });
    const { unmount } = render(<PrintResultsModal printerId="1" pending={pending} onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByLabelText(/clear the waiver/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear plate' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/printers/1/plate-cleared')[0]?.body).toEqual(
      { job_id: 9, clear_waiver: true, results: [] }));
    unmount();
    render(<PrintResultsModal printerId="1" pending={pending} onClose={() => {}} onDone={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Whole plate failed' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear plate' }));
    await waitFor(() => expect(api.to('POST', '/api/v1/printers/1/plate-cleared')[1]?.body).toEqual(
      { job_id: 9, clear_waiver: false, results: [{ project_item_id: 50, quantity_failed: 1 }] }));
  });
});
