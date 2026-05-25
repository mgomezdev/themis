import { describe, it, expect } from 'vitest';
import { fmtTime, fmtClock, matColor } from './helpers';

describe('fmtTime', () => {
  it('formats hours and minutes', () => expect(fmtTime(90)).toBe('1h 30m'));
  it('formats minutes only', () => expect(fmtTime(45)).toBe('45m'));
  it('formats hours only', () => expect(fmtTime(120)).toBe('2h'));
  it('handles null', () => expect(fmtTime(null)).toBe('—'));
});

describe('fmtClock', () => {
  it('zero pads', () => expect(fmtClock(65)).toBe('01:05'));
  it('handles null', () => expect(fmtClock(null)).toBe('--:--'));
});

describe('matColor', () => {
  it('returns blue for PLA', () => expect(matColor('PLA')).toBe('#60a5fa'));
  it('returns slate for PA-CF', () => expect(matColor('PA-CF')).toBe('#94a3b8'));
  it('returns cyan for PETG', () => expect(matColor('PETG')).toBe('#67e8f9'));
});
