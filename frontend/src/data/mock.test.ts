import { describe, it, expect } from 'vitest';
import { PRINTERS, JOBS, ORDERS } from './mock';

describe('mock data', () => {
  it('has 3 printers', () => expect(PRINTERS).toHaveLength(3));
  it('has jobs with eligiblePrinters', () => {
    JOBS.forEach(j => expect(Array.isArray(j.eligiblePrinters)).toBe(true));
  });
  it('has orders with parts', () => {
    ORDERS.forEach(o => expect(o.parts.length).toBeGreaterThan(0));
  });
});
