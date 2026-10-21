import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useSlotConflicts } from './useSlotConflicts';
import type { LoadedFilament } from '../api/printers';

const slot = (n: number, ref: string): LoadedFilament =>
  ({ slot: n, filament_id: null, name: 'PLA', type: 'PLA', subtype: 'standard', color: '#fff', inventory: { provider: 'spoolman', spool_ref: ref } }) as LoadedFilament;
const conflictsCalls = () => vi.mocked(fetch).mock.calls.filter(c => c[0] === '/api/v1/printers/7/slot-conflicts').length;

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ conflicts: [0, 1] }), { status: 200 })));
});

describe('useSlotConflicts', () => {
  it('returns the server verdict and re-reads it when the stored slots change (a save, a telemetry refresh)', async () => {
    const { result, rerender } = renderHook(({ stored }) => useSlotConflicts(7, stored), { initialProps: { stored: [slot(0, '1'), slot(1, '2')] } });
    await waitFor(() => expect(result.current).toEqual([0, 1]));
    expect(conflictsCalls()).toBe(1);
    rerender({ stored: [slot(0, '1'), slot(1, '2')] });                    // same slots, new array: no refetch
    expect(conflictsCalls()).toBe(1);
    vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({ conflicts: [1] }), { status: 200 }));
    rerender({ stored: [slot(0, '9'), slot(1, '2')] });                    // slot 0 was rebound and saved
    await waitFor(() => expect(result.current).toEqual([1]));
    expect(conflictsCalls()).toBe(2);
  });

  it('drops the flag of a slot the form has edited since (the verdict was about the stored one), keeps the others', async () => {
    const stored = [slot(0, '1'), slot(1, '2')];
    const { result, rerender } = renderHook(({ edited }) => useSlotConflicts(7, stored, edited), { initialProps: { edited: stored } });
    await waitFor(() => expect(result.current).toEqual([0, 1]));
    rerender({ edited: [slot(0, '8'), stored[1]] });                       // slot 0 rebound in the form, not saved yet
    expect(result.current).toEqual([1]);
    rerender({ edited: stored });                                          // edit reverted: the verdict applies again
    expect(result.current).toEqual([0, 1]);
  });

  it('clears the verdict when a re-read fails, and does not ask for a printer that does not exist', async () => {
    const { result, rerender } = renderHook(({ stored }) => useSlotConflicts(7, stored), { initialProps: { stored: [slot(0, '1')] } });
    await waitFor(() => expect(result.current).toEqual([0, 1]));
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('down'); }));
    rerender({ stored: [slot(0, '2')] });
    await waitFor(() => expect(result.current).toEqual([]));
    const before = vi.mocked(fetch).mock.calls.length;
    renderHook(() => useSlotConflicts(NaN, [], undefined, false));
    expect(vi.mocked(fetch).mock.calls.length).toBe(before);
  });

  it('re-reads when the hardware first reports a hand-entered slot (same tuple, now reported)', async () => {
    const { rerender } = renderHook(({ stored }) => useSlotConflicts(7, stored), { initialProps: { stored: [slot(0, '1')] } });
    await waitFor(() => expect(conflictsCalls()).toBe(1));
    rerender({ stored: [{ ...slot(0, '1'), reported: true } as LoadedFilament] });
    await waitFor(() => expect(conflictsCalls()).toBe(2));
  });
});
