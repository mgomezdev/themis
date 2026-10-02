import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AnalyticsScreen, fmtHours } from './AnalyticsScreen';
import { Reply, stubFetch } from '../test/fetchStub';
import type { FleetAnalytics } from '../api/analytics';

// Fixed "today" (UTC): presets are computed from it. Only Date is faked — never timers (Testing Library waitFor).
const URL_30 = '/api/v1/fleet/analytics?start=2026-09-02&end=2026-10-01';
const URL_7 = '/api/v1/fleet/analytics?start=2026-09-25&end=2026-10-01';
const URL_90 = '/api/v1/fleet/analytics?start=2026-07-04&end=2026-10-01';

const DATA: FleetAnalytics = {
  range: { start: '2026-09-02', end: '2026-10-01', days: 30 },
  totals: { completed: 8, failed: 2, cancelled: 1, success_rate: 80, print_seconds: 36000, filament_grams: 1500, filament_cost: 42.5 },
  printers: [
    { printer_id: 1, name: 'Alpha', completed: 6, failed: 0, cancelled: 0, success_rate: 100, print_seconds: 7200,
      filament_grams: 900, filament_cost: 30, utilization_pct: 12.5 },
    { printer_id: 2, name: 'Bravo', completed: 2, failed: 2, cancelled: 1, success_rate: 50, print_seconds: 1800,
      filament_grams: 600, filament_cost: null, utilization_pct: 0.3 },
  ],
  materials: [{ material: 'PLA', grams: 1200, jobs: 6 }, { material: 'PETG', grams: 300, jobs: 1 }],
};

beforeEach(() => vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-10-01T15:00:00Z') }));
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('AnalyticsScreen', () => {
  it('requests the last 30 UTC days by default and renders KPIs, printers and materials', async () => {
    const f = stubFetch({ [`GET ${URL_30}`]: DATA });
    render(<AnalyticsScreen />);

    await screen.findByTestId('kpi-jobs');
    expect(f.to('GET', URL_30)).toHaveLength(1);
    expect(within(screen.getByTestId('kpi-jobs')).getByText('8')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-jobs')).getByText('8 done · 2 failed · 1 cancelled')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-success')).getByText('80%')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-hours')).getByText('10 h')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-grams')).getByText('1.50 kg')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-cost')).getByText('$42.50')).toBeTruthy();
    expect(screen.getByTestId('analytics-range').textContent).toContain('2026-09-02 → 2026-10-01');

    const bravo = within(screen.getByTestId('printer-row-2'));
    expect(bravo.getByText('Bravo')).toBeTruthy();
    expect(bravo.getByText('50%')).toBeTruthy();
    expect(bravo.getByText('0.3%')).toBeTruthy();
    expect(within(screen.getByTestId('printer-row-1')).getByText('100%')).toBeTruthy();

    expect(within(screen.getByTestId('material-PLA')).getByText('1.20 kg · 6 jobs')).toBeTruthy();
    expect(within(screen.getByTestId('material-PETG')).getByText('300 g · 1 job')).toBeTruthy();
  });

  it('refetches with the preset range when 7 or 90 days is picked', async () => {
    const f = stubFetch({ [`GET ${URL_30}`]: DATA, [`GET ${URL_7}`]: DATA, [`GET ${URL_90}`]: DATA });
    const user = userEvent.setup();
    render(<AnalyticsScreen />);
    await screen.findByTestId('kpi-jobs');

    await user.click(screen.getByRole('button', { name: '7 days' }));
    await waitFor(() => expect(f.to('GET', URL_7)).toHaveLength(1));
    expect(screen.getByRole('button', { name: '7 days' }).getAttribute('aria-pressed')).toBe('true');

    await user.click(screen.getByRole('button', { name: '90 days' }));
    await waitFor(() => expect(f.to('GET', URL_90)).toHaveLength(1));
  });

  it('custom range sends the picked dates; an inverted range shows an error and sends nothing', async () => {
    const custom = '/api/v1/fleet/analytics?start=2026-09-10&end=2026-09-20';
    const f = stubFetch({ [`GET ${URL_30}`]: DATA, [`GET ${custom}`]: DATA });
    const user = userEvent.setup();
    render(<AnalyticsScreen />);
    await screen.findByTestId('kpi-jobs');

    await user.click(screen.getByRole('button', { name: 'Custom' }));
    const startInput = screen.getByLabelText('Start date');
    const endInput = screen.getByLabelText('End date');
    await user.clear(startInput); await user.type(startInput, '2026-09-10');
    await user.clear(endInput); await user.type(endInput, '2026-09-20');
    await waitFor(() => expect(f.to('GET', custom)).toHaveLength(1));

    const before = f.calls.length;
    await user.clear(endInput); await user.type(endInput, '2026-09-01');
    await screen.findByText('End date must not be before the start date.');
    expect(f.calls.length).toBe(before);
    expect(screen.queryByTestId('kpi-jobs')).toBeNull();
  });

  it('shows dashes, not zeros, when there is no rate or recorded cost', async () => {
    stubFetch({ [`GET ${URL_30}`]: {
      ...DATA,
      totals: { completed: 0, failed: 0, cancelled: 3, success_rate: null, print_seconds: 0, filament_grams: 0, filament_cost: null },
      printers: [], materials: [],
    } });
    render(<AnalyticsScreen />);

    await screen.findByTestId('kpi-jobs');
    expect(within(screen.getByTestId('kpi-success')).getByText('—')).toBeTruthy();
    expect(within(screen.getByTestId('kpi-cost')).getByText('—')).toBeTruthy();
    expect(screen.getByText('No printers yet.')).toBeTruthy();
    expect(screen.getByText('No filament usage recorded in this range.')).toBeTruthy();
  });

  it("surfaces the API's error detail", async () => {
    stubFetch({ [`GET ${URL_30}`]: new Reply(422, { detail: 'range may not exceed 366 days' }) });
    render(<AnalyticsScreen />);

    expect((await screen.findByRole('alert')).textContent).toBe('range may not exceed 366 days');
  });
});

describe('fmtHours', () => {
  it.each([[0, '0.0 h'], [5400, '1.5 h'], [36000, '10 h'], [360000, '100 h']])('%s s -> %s', (s, out) => {
    expect(fmtHours(s)).toBe(out);
  });
});
