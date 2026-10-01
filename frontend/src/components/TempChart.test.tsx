import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TempChart } from './TempChart';
import type { TempSample } from '../lib/tempHistory';

const WINDOW = 30 * 60_000;
const at = (t: number, nozzle: number | null, bed: number | null = null, chamber: number | null = null): TempSample => ({ t, nozzle, bed, chamber });

describe('TempChart', () => {
  it('asks for more data until there are two samples', () => {
    render(<TempChart history={[at(0, 200)]} windowMs={WINDOW} />);
    expect(screen.getByTestId('temp-chart-empty')).toBeTruthy();
  });

  it('draws one line per series that has data, omitting absent ones', () => {
    const { container } = render(
      <TempChart history={[at(0, 25, 24), at(60_000, 200, 60)]} windowMs={WINDOW} />);
    expect([...container.querySelectorAll('polyline')].map(p => p.getAttribute('data-series'))).toEqual(['nozzle', 'bed']);
    expect(screen.getByText(/Nozzle 200°C/)).toBeTruthy();
    expect(screen.getByText(/last 30 min/)).toBeTruthy();
  });

  it('places the newest sample at the right edge and a hotter one higher up', () => {
    const { container } = render(<TempChart history={[at(0, 50), at(WINDOW, 250)]} windowMs={WINDOW} />);
    const pts = container.querySelector('polyline')!.getAttribute('points')!.split(' ').map(p => p.split(',').map(Number));
    expect(pts[1][0]).toBeGreaterThan(pts[0][0]);        // later → further right
    expect(pts[1][1]).toBeLessThan(pts[0][1]);           // hotter → smaller y (higher on screen)
  });
});
