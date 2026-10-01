import { describe, it, expect } from 'vitest';
import { startsIn, toLocalInput, fromLocalInput } from './schedule';

const NOW = Date.parse('2026-10-01T12:00:00Z');

describe('startsIn', () => {
  it.each([
    ['2026-10-01T12:00:30Z', 'in 1m'],        // sub-minute rounds up, never "in 0m"
    ['2026-10-01T12:40:00Z', 'in 40m'],
    ['2026-10-01T14:15:00Z', 'in 2h 15m'],
    ['2026-10-04T16:00:00Z', 'in 3d 4h'],
  ])('%s → %s', (iso, expected) => expect(startsIn(iso, NOW)).toBe(expected));

  it('is null when unset, already due, or unparseable', () => {
    expect(startsIn(null, NOW)).toBeNull();
    expect(startsIn(undefined, NOW)).toBeNull();
    expect(startsIn('2026-10-01T12:00:00Z', NOW)).toBeNull();
    expect(startsIn('2026-09-30T00:00:00Z', NOW)).toBeNull();
    expect(startsIn('garbage', NOW)).toBeNull();
  });
});

describe('datetime-local conversion', () => {
  it('round-trips an instant through the local input format', () => {
    const iso = '2030-01-01T22:00:00.000Z';
    expect(fromLocalInput(toLocalInput(iso))).toBe(iso);
  });
  it('maps empty/invalid to empty/null', () => {
    expect(toLocalInput(null)).toBe('');
    expect(toLocalInput('nope')).toBe('');
    expect(fromLocalInput('')).toBeNull();
    expect(fromLocalInput('nope')).toBeNull();
  });
});
