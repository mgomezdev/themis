import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SaveSliceControl, SliceCacheDebug, SliceCacheMarkers } from './SliceCache';
import type { ApiJob, SliceCacheInfo } from '../api/queue';
import { Reply, stubFetch } from '../test/fetchStub';

afterEach(() => vi.unstubAllGlobals());

const job = (over: Partial<ApiJob> = {}) => ({
  id: 9, status: 'queued', save_slice: false, save_slice_name: null, sliced_version_id: null, slice_cache_info: null,
  ...over,
}) as ApiJob;
const saved = (outcome: 'saved' | 'duplicate' | 'failed', error: string | null = null): SliceCacheInfo => ({
  save: { outcome, sliced_version_id: 3, cache_key: 'k', file_id: 4, error, at: 't' },
});

const markers = (j: ApiJob) => {
  const { container } = render(<SliceCacheMarkers job={j} />);
  return [...container.querySelectorAll('.pill')].map(e => e.textContent);
};

describe('SliceCacheMarkers', () => {
  it.each([
    [job(), []],
    [job({ save_slice: true }), ['Saving gcode']],
    [job({ save_slice: true, slice_cache_info: saved('saved') }), ['Gcode saved to library']],
    [job({ save_slice: true, slice_cache_info: saved('duplicate') }), ['Gcode saved to library']],
    [job({ save_slice: true, slice_cache_info: saved('failed', 'disk full') }), ['Gcode save failed']],
    [job({ sliced_version_id: 2 }), ['Used cached gcode']],
    [job({ sliced_version_id: 2, slice_cache_info: { stale: true, stale_reasons: ['presets_changed', 'slicer_version_changed'] } }),
     ['Used cached gcode', 'Stale: presets edited', 'Stale: OrcaSlicer updated']],
  ])('%#', (j, expected) => {
    expect(markers(j)).toEqual(expected);
  });
});

function control(j: ApiJob, presliced = false) {
  const onChange = vi.fn();
  render(<MemoryRouter><SaveSliceControl job={j} presliced={presliced} onChange={onChange} /></MemoryRouter>);
  return onChange;
}

describe('SaveSliceControl', () => {
  it.each(['complete', 'failed', 'cancelled'])('is hidden for a %s job', status => {
    control(job({ status }));
    expect(screen.queryByTestId('save-slice-control')).toBeNull();
  });

  it('is hidden for a pre-sliced file and for a job printing a cached version', () => {
    control(job(), true);
    control(job({ sliced_version_id: 3 }));
    expect(screen.queryByTestId('save-slice-control')).toBeNull();
  });

  it('flags a queued job with its name', async () => {
    const api = stubFetch({ 'PATCH /api/v1/jobs/9/save-slice': job({ save_slice: true, save_slice_name: 'Keep' }) });
    const onChange = control(job());
    await userEvent.type(screen.getByLabelText('Saved gcode name'), ' Keep ');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Save sliced gcode to library' }));

    expect(api.to('PATCH', '/api/v1/jobs/9/save-slice').map(c => c.body)).toEqual([{ save_slice: true, name: 'Keep' }]);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ save_slice: true }));
  });

  it('reads "Save now" once the job is sliced', () => {
    control(job({ status: 'printing' }));
    expect(screen.getByRole('checkbox', { name: 'Save sliced gcode now' })).toBeTruthy();
  });

  it('turning it off sends no name', async () => {
    const api = stubFetch({ 'PATCH /api/v1/jobs/9/save-slice': job() });
    control(job({ save_slice: true, save_slice_name: 'x' }));
    await userEvent.click(screen.getByRole('checkbox'));
    expect(api.to('PATCH', '/api/v1/jobs/9/save-slice').map(c => c.body)).toEqual([{ save_slice: false, name: null }]);
  });

  it('links to the library once saved, and offers a retry after a failure', async () => {
    control(job({ save_slice: true, status: 'printing', slice_cache_info: saved('saved') }));
    expect(screen.getByRole('link', { name: 'open the library' }).getAttribute('href')).toBe('/files');

    const api = stubFetch({ 'PATCH /api/v1/jobs/8/save-slice': job({ id: 8 }) });
    control(job({ id: 8, save_slice: true, status: 'printing', slice_cache_info: saved('failed', 'disk full') }));
    expect(screen.getByText(/Save failed: disk full/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(api.to('PATCH', '/api/v1/jobs/8/save-slice').map(c => c.body)).toEqual([{ save_slice: true, name: null }]);
  });

  it('shows why the server refused', async () => {
    stubFetch({ 'PATCH /api/v1/jobs/9/save-slice': new Reply(409, { detail: 'Job is complete' }) });
    const onChange = control(job());
    await userEvent.click(screen.getByRole('checkbox'));
    expect(await screen.findByText(/409/)).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('SliceCacheDebug', () => {
  it('renders nothing without a decision', () => {
    const { container } = render(<SliceCacheDebug info={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('shows the decision, hashes, staleness and save outcome, and copies the key', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    render(<SliceCacheDebug info={{
      decision: 'hit', reason: null, cache_key: 'abc123', cached_file_hash: 'fh', policy: 'pin_cached',
      preset_content_hash_stored: 'old', preset_content_hash_current: 'new', stale: true,
      stale_reasons: ['presets_changed'], ...saved('failed', 'disk full'),
    }} />);

    const text = screen.getByTestId('slice-cache-debug').textContent ?? '';
    for (const bit of ['abc123', 'hit', 'pin_cached', 'fh', 'old', 'new', 'Stale: presets edited', 'failed', 'disk full']) {
      expect(text).toContain(bit);
    }
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('abc123');
    expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy();
  });
});
