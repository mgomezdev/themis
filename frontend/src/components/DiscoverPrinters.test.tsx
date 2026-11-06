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

  it('overrides announced model and serial, clears a stale model match, and preserves connection fields', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    const announced = { ...BAMBU, plugin_id: 'bambu', model_uuid: 'uuid-p1s' };
    stubFetch({ 'POST /api/v1/printers/discover': { ...RESULT, found: [announced] } });
    render(<DiscoverPrinters pluginId="bambu" onPick={onPick} />);
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Edit details for 192.168.7.20' }));
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('P1S');
    expect((screen.getByLabelText('Serial number') as HTMLInputElement).value).toBe(BAMBU.serial);
    await user.clear(screen.getByLabelText('Model'));
    await user.type(screen.getByLabelText('Model'), ' X1 Carbon ');
    await user.clear(screen.getByLabelText('Serial number'));
    await user.type(screen.getByLabelText('Serial number'), ' TEST-OVERRIDE ');
    expect(screen.getByRole('button', { name: 'Use 192.168.7.20' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Apply details' }));
    expect(screen.getByText('Serial: TEST-OVERRIDE')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenCalledWith({ ...announced, model: 'X1 Carbon', model_uuid: null, serial: 'TEST-OVERRIDE',
      connection_config: { ...announced.connection_config, serial_number: 'TEST-OVERRIDE' } });
    expect(window.localStorage.getItem('themis.discovery.ranges')).toBeNull();
  });

  it('supplements missing serial information without losing the discovered model match', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    const announced = { ...BAMBU, model_uuid: 'uuid-p1s', serial: null, connection_config: { ip_address: BAMBU.ip, port: 8883 } };
    stubFetch({ 'POST /api/v1/printers/discover': { ...RESULT, found: [announced] } });
    render(<DiscoverPrinters pluginId="bambu" onPick={onPick} />);
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Edit details for 192.168.7.20' }));
    await user.type(screen.getByLabelText('Serial number'), 'TEST-SUPPLEMENT');
    await user.click(screen.getByRole('button', { name: 'Apply details' }));
    await user.click(screen.getByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenCalledWith({ ...announced, serial: 'TEST-SUPPLEMENT',
      connection_config: { ...announced.connection_config, serial_number: 'TEST-SUPPLEMENT' } });
  });

  it('cancels draft edits, resets overrides, and clears them on a new scan', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    stubFetch({ 'POST /api/v1/printers/discover': RESULT });
    render(<DiscoverPrinters pluginId="bambu" onPick={onPick} />);
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    const edit = async () => user.click(await screen.findByRole('button', { name: 'Edit details for 192.168.7.20' }));
    await edit();
    await user.clear(screen.getByLabelText('Model'));
    await user.type(screen.getByLabelText('Model'), 'Draft');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenLastCalledWith(BAMBU);
    await edit();
    await user.clear(screen.getByLabelText('Model'));
    await user.type(screen.getByLabelText('Model'), 'Override');
    await user.click(screen.getByRole('button', { name: 'Apply details' }));
    await edit();
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('Override');
    await user.click(screen.getByRole('button', { name: 'Reset to discovered' }));
    await user.click(screen.getByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenLastCalledWith(BAMBU);
    await edit();
    await user.clear(screen.getByLabelText('Serial number'));
    await user.click(screen.getByRole('button', { name: 'Apply details' }));
    await user.click(screen.getByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenLastCalledWith(expect.objectContaining({ serial: null,
      connection_config: expect.objectContaining({ serial_number: '' }) }));
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));
    expect(onPick).toHaveBeenLastCalledWith(BAMBU);
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
