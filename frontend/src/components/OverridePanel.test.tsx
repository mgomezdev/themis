import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OverridePanel } from './OverridePanel';

const SETTINGS = [
  { key: 'enable_support', label: 'Enable supports', value: '1', carried: true },
  { key: 'brim_type', label: 'Brim type', value: 'no_brim', carried: false },
];

describe('OverridePanel', () => {
  it('shows a carried setting as carried (no checkbox) and lets a non-carried one be kept for the job', () => {
    let v: Record<string, string> = {};
    render(<OverridePanel settings={SETTINGS} value={v} onChange={n => { v = n; }} />);
    expect(screen.getByTestId('carried-enable_support').textContent).toContain('carried');
    expect(screen.queryByTestId('override-enable_support')).toBeNull();
    fireEvent.click(screen.getByTestId('override-brim_type'));
    expect(v).toEqual({ brim_type: 'no_brim' });
  });

  it('shows the job value, not the file value, once a setting is a job override', () => {
    render(<OverridePanel settings={SETTINGS} value={{ enable_support: '0', brim_type: 'brim_ears' }} onChange={() => {}} />);
    expect(screen.getByTestId('override-enable_support').closest('label')!.textContent).toContain('0');
    expect(screen.getByTestId('override-brim_type').closest('label')!.textContent).toContain('brim_ears');
    expect(screen.getByTestId('override-brim_type').closest('label')!.textContent).not.toContain('no_brim');
  });
});
