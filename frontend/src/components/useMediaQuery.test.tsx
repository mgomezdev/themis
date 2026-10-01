import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMediaQuery } from './useMediaQuery';

function stubMatchMedia(initial: boolean) {
  let listener: (() => void) | null = null;
  const mq = {
    matches: initial,
    addEventListener: vi.fn((_: string, l: () => void) => { listener = l; }),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal('matchMedia', vi.fn(() => mq));
  return { mq, fire: (matches: boolean) => { mq.matches = matches; listener?.(); } };
}

describe('useMediaQuery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reflects the initial match and follows changes', () => {
    const { fire } = stubMatchMedia(true);
    const { result } = renderHook(() => useMediaQuery('(max-width: 768px)'));
    expect(result.current).toBe(true);

    act(() => fire(false));
    expect(result.current).toBe(false);
  });

  it('unsubscribes on unmount', () => {
    const { mq } = stubMatchMedia(false);
    const { unmount } = renderHook(() => useMediaQuery('(max-width: 768px)'));
    unmount();
    expect(mq.removeEventListener).toHaveBeenCalledTimes(1);
  });

  it('is false when matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined);
    const { result } = renderHook(() => useMediaQuery('(max-width: 768px)'));
    expect(result.current).toBe(false);
  });
});
