import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DiscoverPrinters } from './DiscoverPrinters';
import { stubFetch, Reply } from '../test/fetchStub';

const BAMBU = {
  printer_type: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.20', model: 'P1S', name: 'Bambu-P1S',
  serial: '01P00A111111111', connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111' },
  note: null, already_added: false,
};
const MOON = {
  printer_type: 'snapmaker_extended', display_name: 'Snapmaker U1 (Extended)', ip: '192.168.7.30', model: 'Moonraker / Klipper',
  name: null, serial: null, connection_config: { ip_address: '192.168.7.30', port: 7125 }, note: 'Requires an API key', already_added: true,
};
const RESULT = { ranges: ['192.168.7.0/24'], scanned: 254, truncated: false, found: [BAMBU, MOON] };

beforeEach(() => { try { window.localStorage.clear(); } catch { /* ignore */ } });
afterEach(() => vi.unstubAllGlobals());

async function openAndScan(user: ReturnType<typeof userEvent.setup>, range = '192.168.7.0/24') {
  await user.click(screen.getByRole('button', { name: /scan network/i }));
  if (range) await user.type(screen.getByLabelText('Network ranges'), range);
  await user.click(screen.getByRole('button', { name: 'Scan' }));
}

describe('DiscoverPrinters', () => {
  it('is collapsed until asked for', () => {
    render(<DiscoverPrinters onPick={() => {}} />);
    expect(screen.queryByLabelText('Network ranges')).toBeNull();
    expect(screen.getByRole('button', { name: /scan network for printers/i })).toBeTruthy();
  });

  it('sends the typed ranges (split on commas/spaces), lists what is found and flags added printers and notes', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({ 'POST /api/v1/printers/discover': RESULT });
    render(<DiscoverPrinters onPick={() => {}} />);

    await openAndScan(user, '192.168.7.0/24, 192.168.8.5');

    expect(calls[0].body).toEqual({ ranges: ['192.168.7.0/24', '192.168.8.5'] });
    expect(await screen.findByText(/Bambu Lab · P1S \(Bambu-P1S\)/)).toBeTruthy();
    expect(screen.getByText('192.168.7.20')).toBeTruthy();
    const moon = screen.getByText('192.168.7.30').closest('tr')!;
    expect(within(moon).getByText('already added')).toBeTruthy();
    expect(within(moon).getByText('Requires an API key')).toBeTruthy();
    expect(within(moon).getByRole('button', { name: 'Use 192.168.7.30' }).textContent).toBe('Add again');
    expect(screen.getByText(/Probed 254 addresses in 192.168.7.0\/24/)).toBeTruthy();
  });

  it('an empty range box scans the server\'s own network (empty list)', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({ 'POST /api/v1/printers/discover': { ...RESULT, found: [] } });
    render(<DiscoverPrinters onPick={() => {}} />);
    await openAndScan(user, '');
    expect(calls[0].body).toEqual({ ranges: [] });
    expect(await screen.findByTestId('discover-none')).toBeTruthy();
  });

  it('hands the chosen printer to the caller', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    stubFetch({ 'POST /api/v1/printers/discover': RESULT });
    render(<DiscoverPrinters onPick={onPick} />);
    await openAndScan(user);

    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));

    expect(onPick).toHaveBeenCalledWith(BAMBU);
  });

  it('warns when the scan stopped early and shows a server rejection', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': { ...RESULT, truncated: true } });
    const { unmount } = render(<DiscoverPrinters onPick={() => {}} />);
    await openAndScan(user);
    expect(await screen.findByText(/stopped early; narrow the range/)).toBeTruthy();
    unmount();

    stubFetch({ 'POST /api/v1/printers/discover': new Reply(422, { detail: '8.8.8.0/24 is not a private network' }) });
    render(<DiscoverPrinters onPick={() => {}} />);
    await openAndScan(user, '8.8.8.0/24');
    expect((await screen.findByRole('alert')).textContent).toBe('8.8.8.0/24 is not a private network');   // the server's reason, not "422 {…}"
  });

  it('remembers the range for next time', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': RESULT });
    const first = render(<DiscoverPrinters onPick={() => {}} />);
    await openAndScan(user, '192.168.7.0/24');
    await screen.findByText(/Probed/);
    first.unmount();

    render(<DiscoverPrinters onPick={() => {}} />);
    await user.click(screen.getByRole('button', { name: /scan network/i }));
    expect((screen.getByLabelText('Network ranges') as HTMLInputElement).value).toBe('192.168.7.0/24');
  });
});
