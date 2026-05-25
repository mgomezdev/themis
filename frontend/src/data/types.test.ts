import { describe, it, expectTypeOf } from 'vitest';
import type { Printer, Job, Order, Filament, FileEntry } from './types';

describe('data types', () => {
  it('Printer has required fields', () => {
    expectTypeOf<Printer>().toHaveProperty('id');
    expectTypeOf<Printer>().toHaveProperty('status');
    expectTypeOf<Printer>().toHaveProperty('capabilities');
  });
  it('Job has eligiblePrinters', () => {
    expectTypeOf<Job>().toHaveProperty('eligiblePrinters');
  });
});
