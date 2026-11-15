import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAutoDismiss } from './useAutoDismiss';

// No Testing Library waitFor here, so fake timers are safe.
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(initial: unknown) {
  const onExpire = vi.fn();
  const hook = renderHook(({ v }) => useAutoDismiss(v, onExpire, 1000), { initialProps: { v: initial } });
  return { onExpire, ...hook };
}

describe('useAutoDismiss', () => {
  it('expires once, `ms` after the value turns truthy', () => {
    const { onExpire } = setup({ a: 1 });
    vi.advanceTimersByTime(999);
    expect(onExpire).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onExpire).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(5000);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('never fires for a falsy value', () => {
    const { onExpire } = setup(null);
    vi.advanceTimersByTime(5000);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('restarts the wait for a new value', () => {
    const { onExpire, rerender } = setup({ a: 1 });
    vi.advanceTimersByTime(800);
    rerender({ v: { a: 2 } });
    vi.advanceTimersByTime(800);
    expect(onExpire).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('does not restart when re-rendered with the same value', () => {
    const v = { a: 1 };
    const { onExpire, rerender } = setup(v);
    vi.advanceTimersByTime(800);
    rerender({ v });
    vi.advanceTimersByTime(200);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('cancels when the value turns falsy or the component unmounts', () => {
    const a = setup({ a: 1 });
    a.rerender({ v: null });
    const b = setup({ b: 1 });
    b.unmount();
    vi.advanceTimersByTime(5000);
    expect(a.onExpire).not.toHaveBeenCalled();
    expect(b.onExpire).not.toHaveBeenCalled();
  });

  it('calls the latest callback', () => {
    const first = vi.fn();
    const second = vi.fn();
    const v = { a: 1 };
    const { rerender } = renderHook(({ cb }) => useAutoDismiss(v, cb, 1000), { initialProps: { cb: first } });
    rerender({ cb: second });
    vi.advanceTimersByTime(1000);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
