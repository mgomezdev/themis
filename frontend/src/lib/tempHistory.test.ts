import { describe, it, expect } from 'vitest';
import { appendSample, type TempSample } from './tempHistory';

const s = (t: number, nozzle: number | null = 200): TempSample => ({ t, nozzle, bed: 60, chamber: null });

describe('appendSample', () => {
  it('appends in order', () => {
    expect(appendSample([s(0)], s(10_000)).map(x => x.t)).toEqual([0, 10_000]);
  });

  it('ignores a sample less than 5 s after the last (telemetry can arrive many times a second)', () => {
    const h = [s(0)];
    expect(appendSample(h, s(4_999))).toBe(h);
    expect(appendSample(h, s(5_000))).toHaveLength(2);
  });

  it('drops samples older than the window, keeping the boundary', () => {
    const h = [s(0), s(60_000), s(120_000)];
    expect(appendSample(h, s(180_000), 120_000).map(x => x.t)).toEqual([60_000, 120_000, 180_000]);
  });

  it('does not mutate the previous history', () => {
    const h = [s(0)];
    appendSample(h, s(10_000));
    expect(h).toHaveLength(1);
  });
});
