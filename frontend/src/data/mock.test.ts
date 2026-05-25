import { describe, it, expect } from 'vitest';
import { PRINTERS, JOBS, ORDERS, FILAMENTS, FILES } from './mock';

describe('mock data', () => {
  it('has 3 printers', () => expect(PRINTERS).toHaveLength(3));
  it('has jobs with eligiblePrinters', () => {
    JOBS.forEach(j => expect(Array.isArray(j.eligiblePrinters)).toBe(true));
  });
  it('has orders with parts', () => {
    ORDERS.forEach(o => expect(o.parts.length).toBeGreaterThan(0));
  });
  it('has filaments with profiles', () => {
    FILAMENTS.forEach(f => expect(Array.isArray(f.profiles)).toBe(true));
  });
  it('has files with folders', () => {
    FILES.forEach(f => expect(typeof f.folder).toBe('string'));
  });
});
