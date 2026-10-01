import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AlarmsScreen } from './AlarmsScreen';
import { stubFetch, Reply } from '../test/fetchStub';

const alarm = (over: object) => ({
  id: 1, printer_id: 1, printer_name: 'Atlas', code: 'HMS_0700_0100_0002_0001', severity: 'error',
  message: 'AMS reported HMS 0700_0100_0002_0001', source: 'hms', help_url: 'https://wiki.example/hms/0700',
  first_seen: '2026-10-01T12:00:00+00:00', last_seen: '2026-10-01T12:05:00+00:00', resolved_at: null, acknowledged_at: null,
  active: true, ...over,
});

afterEach(() => vi.unstubAllGlobals());

function show(path = '/alarms') {
  return render(<MemoryRouter initialEntries={[path]}><AlarmsScreen /></MemoryRouter>);
}
const SETTINGS = { 'GET /api/v1/alarms/settings': { min_severity: 'warning', severities: ['info', 'warning', 'error', 'fatal'] } };

describe('AlarmsScreen', () => {
  it('lists unacknowledged alarms by default with severity, printer, text, code and wiki link', async () => {
    const { calls } = stubFetch({ ...SETTINGS, 'GET /api/v1/alarms?status=unacknowledged': [alarm({}), alarm({ id: 2, severity: 'fatal', printer_name: 'Borealis', code: 'KLIPPER_SHUTDOWN', message: 'Heater fault', help_url: null })] });
    show();

    const row = await screen.findByTestId('alarm-1');
    expect(within(row).getByTestId('severity-error')).toBeTruthy();
    expect(within(row).getByText('Atlas')).toBeTruthy();
    expect(within(row).getByText('AMS reported HMS 0700_0100_0002_0001')).toBeTruthy();
    expect(within(row).getByRole('link', { name: 'Bambu wiki' }).getAttribute('href')).toBe('https://wiki.example/hms/0700');
    const second = screen.getByTestId('alarm-2');
    expect(within(second).getByTestId('severity-fatal')).toBeTruthy();
    expect(within(second).queryByRole('link')).toBeNull();
    expect(calls.some(c => c.url === '/api/v1/alarms?status=unacknowledged')).toBe(true);
  });

  it('switches between needs-attention, active and history', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      ...SETTINGS,
      'GET /api/v1/alarms?status=unacknowledged': [],
      'GET /api/v1/alarms?status=active': [alarm({ acknowledged_at: '2026-10-01T13:00:00+00:00' })],
      'GET /api/v1/alarms?status=all': [alarm({ id: 3, active: false, resolved_at: '2026-10-01T14:00:00+00:00' })],
    });
    show();
    expect(await screen.findByTestId('alarms-empty')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Active' }));
    expect(await screen.findByText('acknowledged')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Acknowledge HMS/ })).toBeNull();               // already acknowledged

    await user.click(screen.getByRole('button', { name: 'History' }));
    expect(await screen.findByText(/resolved 2026-10-01 14:00/)).toBeTruthy();
    expect(calls.map(c => c.url)).toContain('/api/v1/alarms?status=all');
  });

  it('acknowledges one alarm and reloads', async () => {
    const user = userEvent.setup();
    let acked = false;
    const { calls } = stubFetch({
      ...SETTINGS,
      'GET /api/v1/alarms?status=unacknowledged': () => (acked ? [] : [alarm({})]),
      'POST /api/v1/alarms/1/acknowledge': () => { acked = true; return alarm({ acknowledged_at: 'now' }); },
    });
    show();

    await user.click(await screen.findByRole('button', { name: 'Acknowledge HMS_0700_0100_0002_0001' }));

    await waitFor(() => expect(screen.getByTestId('alarms-empty')).toBeTruthy());
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(1);
  });

  it('acknowledge-all is scoped to the printer filter and disabled when nothing needs it', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      ...SETTINGS,
      'GET /api/v1/alarms?status=unacknowledged&printer_id=7': [alarm({ printer_id: 7 })],
      'POST /api/v1/alarms/acknowledge-all?printer_id=7': { acknowledged: 1 },
    });
    show('/alarms?printer=7');

    await user.click(await screen.findByRole('button', { name: 'Acknowledge all' }));

    await waitFor(() => expect(calls.some(c => c.url === '/api/v1/alarms/acknowledge-all?printer_id=7')).toBe(true));
    expect(screen.getByRole('button', { name: /show all/i })).toBeTruthy();
  });

  it('acknowledge-all is disabled with no unacknowledged alarms', async () => {
    stubFetch({ ...SETTINGS, 'GET /api/v1/alarms?status=unacknowledged': [] });
    show();
    await screen.findByTestId('alarms-empty');
    expect((screen.getByRole('button', { name: 'Acknowledge all' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('loads and saves the notification severity threshold', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'GET /api/v1/alarms/settings': { min_severity: 'error', severities: ['info', 'warning', 'error', 'fatal'] },
      'GET /api/v1/alarms?status=unacknowledged': [],
      'PUT /api/v1/alarms/settings': { min_severity: 'fatal' },
    });
    show();
    const select = await screen.findByLabelText(/send webhook/i) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('error'));

    await user.selectOptions(select, 'fatal');

    await waitFor(() => expect(calls.find(c => c.method === 'PUT')?.body).toEqual({ min_severity: 'fatal' }));
  });

  it('puts the threshold back and says why when saving it fails', async () => {
    const user = userEvent.setup();
    stubFetch({
      'GET /api/v1/alarms/settings': { min_severity: 'error', severities: ['info', 'warning', 'error', 'fatal'] },
      'GET /api/v1/alarms?status=unacknowledged': [],
      'PUT /api/v1/alarms/settings': new Reply(500, 'nope'),
    });
    show();
    const select = await screen.findByLabelText(/send webhook/i) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('error'));

    await user.selectOptions(select, 'fatal');

    expect((await screen.findByRole('alert')).textContent).toMatch(/500/);
    await waitFor(() => expect(select.value).toBe('error'));          // not left showing a value that was never stored
  });

  it('tells the user which notification event must be ticked', async () => {
    stubFetch({ ...SETTINGS, 'GET /api/v1/alarms?status=unacknowledged': [] });
    show();
    expect((await screen.findByText(/printer\.alarm/)).textContent).toMatch(/ticked/);
  });

  it('shows a failed acknowledge inline', async () => {
    const user = userEvent.setup();
    stubFetch({
      ...SETTINGS,
      'GET /api/v1/alarms?status=unacknowledged': [alarm({})],
      'POST /api/v1/alarms/1/acknowledge': new Reply(500, 'nope'),
    });
    show();
    await user.click(await screen.findByRole('button', { name: /Acknowledge HMS/ }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/500/);
  });
});
