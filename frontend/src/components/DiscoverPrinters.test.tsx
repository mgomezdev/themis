import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DiscoverPrinters } from './DiscoverPrinters';
import type { ConnectionField, DiscoveredPrinter } from '../api/printers';
import { stubFetch, Reply, type Call } from '../test/fetchStub';
import { IP_FIELD, printerType } from '../test/printerTypes';

const quick = (name: string, label: string, over: Partial<ConnectionField> = {}): ConnectionField =>
  ({ ...IP_FIELD, name, label, quick_add: true, ...over });

const P1S = printerType({
  plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S',
  model_uuid: 'uuid-p1s',
  connection_fields: [IP_FIELD, quick('serial_number', 'Serial Number'), quick('access_code', 'Access Code', { field_type: 'password' })],
});
const A1 = printerType({ ...P1S, model_id: 'a1', display_name: 'A1', model_uuid: 'uuid-a1' });
const CENTAURI = printerType({
  plugin_id: 'elegoo_centauri', manufacturer_id: 'elegoo', manufacturer_name: 'Elegoo', model_id: 'centauri',
  display_name: 'Centauri Carbon', model_uuid: 'uuid-cc', connection_fields: [IP_FIELD, { ...IP_FIELD, name: 'port', label: 'Port', required: false }],
});
const TYPES = [P1S, A1, CENTAURI];

const found = (over: Partial<DiscoveredPrinter>): DiscoveredPrinter => ({
  model_uuid: null, printer_type: 'bambu', plugin_id: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.20', model: 'P1S', name: null,
  serial: null, connection_config: {}, note: null, already_added: false, added_printer: null, ip_in_use_by: null, ...over,
});
const BAMBU = found({
  model_uuid: 'uuid-p1s', name: 'Bambu-P1S', serial: '01P00A111111111',
  connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111' },
});
const ELEGOO = (ip: string, over: Partial<DiscoveredPrinter> = {}) => found({
  printer_type: 'elegoo_centauri', plugin_id: 'elegoo_centauri', display_name: 'Elegoo', ip, model: 'Centauri Carbon',
  model_uuid: 'uuid-cc', connection_config: { ip_address: ip, port: 3030 }, ...over,
});
const result = (...rows: DiscoveredPrinter[]) => ({ ranges: ['192.168.7.0/24'], scanned: 254, truncated: false, found: rows });

const saved = (id: number, over: Record<string, unknown> = {}) => ({ id, configured: true, config_error: null, connection_config: {}, ...over });

beforeEach(() => { try { window.localStorage.clear(); } catch { /* ignore */ } });
afterEach(() => vi.unstubAllGlobals());

async function openAndScan(user: ReturnType<typeof userEvent.setup>, range = '192.168.7.0/24') {
  await user.click(screen.getByRole('button', { name: /scan network/i }));
  if (range) await user.type(screen.getByLabelText('Network ranges'), range);
  await user.click(screen.getByRole('button', { name: 'Scan' }));
}
const rowOf = (ip: string, nth = 0) => screen.getAllByText(ip, { selector: 'td' })[nth].closest('tr')!;
const posts = (calls: Call[]) => calls.filter(c => c.method === 'POST' && c.url === '/api/v1/printers');

describe('DiscoverPrinters', () => {
  it('is collapsed until asked for', () => {
    render(<DiscoverPrinters />);
    expect(screen.queryByLabelText('Network ranges')).toBeNull();
    expect(screen.getByRole('button', { name: /scan network for printers/i })).toBeTruthy();
  });

  it('sends the typed ranges, lists what is found grouped by plugin, with notes and the probe summary', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(BAMBU, ELEGOO('192.168.7.40', { note: 'Firmware is old' })),
      'GET /api/v1/printers/types': TYPES,
    });
    render(<DiscoverPrinters />);
    await openAndScan(user, '192.168.7.0/24, 192.168.8.5');

    expect(calls[0].body).toEqual({ ranges: ['192.168.7.0/24', '192.168.8.5'] });
    expect(await screen.findByRole('region', { name: 'Found Bambu Lab printers' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Found Elegoo printers' })).toBeTruthy();
    expect((screen.getByLabelText('Nickname for 192.168.7.20') as HTMLInputElement).value).toBe('Bambu-P1S');
    expect((screen.getByLabelText('Nickname for 192.168.7.40') as HTMLInputElement).value).toBe('Centauri Carbon 40');
    expect((screen.getByLabelText('Model for 192.168.7.20') as HTMLInputElement).value).toBe('Bambu Lab P1S');
    expect(within(rowOf('192.168.7.40')).getByText('Firmware is old')).toBeTruthy();
    expect(screen.getByText(/Probed 254 addresses in 192.168.7.0\/24/)).toBeTruthy();
  });

  it('an empty range box scans the server\'s own network (empty list)', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({ 'POST /api/v1/printers/discover': result(), 'GET /api/v1/printers/types': TYPES });
    render(<DiscoverPrinters />);
    await openAndScan(user, '');
    expect(calls[0].body).toEqual({ ranges: [] });
    expect(await screen.findByTestId('discover-none')).toBeTruthy();
  });

  it('renders quick-add inputs only from the plugin\'s quick_add metadata, tagging scanned values', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': result(BAMBU, ELEGOO('192.168.7.40')), 'GET /api/v1/printers/types': TYPES });
    render(<DiscoverPrinters />);
    await openAndScan(user);

    const bambu = rowOf('192.168.7.20');
    expect((within(bambu).getByLabelText('Serial Number for 192.168.7.20') as HTMLInputElement).value).toBe('01P00A111111111');
    expect(within(bambu).getByText('· from scan')).toBeTruthy();
    expect((within(bambu).getByLabelText('Access Code for 192.168.7.20') as HTMLInputElement).type).toBe('password');
    expect(within(bambu).getByTestId('row-status').textContent).toBe('Missing Access Code');
    const elegoo = rowOf('192.168.7.40');
    expect(within(elegoo).getByText('Nothing to enter')).toBeTruthy();
    expect(within(elegoo).queryByLabelText(/Port for/)).toBeNull();                       // not quick-add: not on the row
    expect(within(elegoo).queryByRole('button', { name: /^Test / })).toBeNull();           // nothing to test before adding
    expect(within(elegoo).getByTestId('row-status').textContent).toBe('Ready to add');
  });

  it('batch add keeps the results on screen and reports each row; at most 4 creates run at once', async () => {
    const user = userEvent.setup();
    const ips = ['192.168.7.41', '192.168.7.42', '192.168.7.43', '192.168.7.44', '192.168.7.45', '192.168.7.46', '192.168.7.47'];
    let inFlight = 0;
    let peak = 0;
    const releases: (() => void)[] = [];
    const onAdded = vi.fn();
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(...ips.map(ip => ELEGOO(ip))),
      'GET /api/v1/printers/types': TYPES,
      'POST /api/v1/printers': (c: Call) => {
        const ip = (c.body as { connection_config: { ip_address: string } }).connection_config.ip_address;
        if (ip === '192.168.7.42') return new Reply(409, { detail: 'C1 already uses 192.168.7.42 as Centauri Carbon' });
        if (ip === '192.168.7.43') return new Reply(500, { detail: 'database is locked' });
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        const id = Number(ip.split('.').pop());
        return new Promise(resolve => releases.push(() => { inFlight -= 1; resolve(saved(id, { configured: false })); }));
      },
      ...Object.fromEntries(ips.map(ip => {
        const id = Number(ip.split('.').pop());
        return [`GET /api/v1/printers/${id}`, id === 44 ? saved(id, { configured: false, config_error: 'Couldn\'t reach 192.168.7.44:3030' }) : saved(id)];
      })),
    });
    render(<DiscoverPrinters onAdded={onAdded} />);
    await openAndScan(user);

    await user.click(await screen.findByRole('button', { name: 'Add 7 printers' }));
    await waitFor(() => expect(releases.length).toBe(4));                                 // 5 slow creates, 4 slots
    await new Promise(r => setTimeout(r, 20));
    expect(peak).toBe(4);
    while (releases.length) { releases.shift()!(); await new Promise(r => setTimeout(r, 0)); }

    const chip = (ip: string) => within(rowOf(ip)).getByTestId('row-status').textContent;
    await waitFor(() => expect(chip('192.168.7.47')).toBe('Added · configured'));
    expect(chip('192.168.7.41')).toBe('Added · configured');
    expect(chip('192.168.7.42')).toBe('Already added');
    expect(within(rowOf('192.168.7.43')).getByRole('alert').textContent).toBe('database is locked');
    expect((within(rowOf('192.168.7.43')).getByLabelText('Select 192.168.7.43') as HTMLInputElement).checked).toBe(true);
    expect(chip('192.168.7.44')).toBe('Added · not configured: Couldn\'t reach 192.168.7.44:3030');
    expect(posts(calls)).toHaveLength(7);
    expect(posts(calls)[0].body).toMatchObject({ plugin_id: 'elegoo_centauri', manufacturer_id: 'elegoo', model_id: 'centauri',
      name: 'Centauri Carbon 41', connection_config: { ip_address: '192.168.7.41', port: 3030 } });
    expect(onAdded).toHaveBeenCalledTimes(5);
    expect(screen.getByTestId('discover-printers')).toBeTruthy();                             // nothing navigated away
    expect(screen.getByRole('button', { name: 'Add 1 printer' })).toBeTruthy();              // the failed row can be retried
    await user.click(screen.getByRole('button', { name: 'All new' }));
    expect(screen.getByRole('button', { name: 'Add 1 printer' })).toBeTruthy();              // ...but not the "Already added" one
  });

  it('a batch\'s creates are not held back by the rows still waiting on their connection test', async () => {
    const user = userEvent.setup();
    const ips = ['192.168.7.51', '192.168.7.52', '192.168.7.53', '192.168.7.54', '192.168.7.55', '192.168.7.56'];
    let n = 60;
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(...ips.map(ip => ELEGOO(ip))), 'GET /api/v1/printers/types': TYPES,
      'POST /api/v1/printers': () => saved(++n, { configured: false }),
      ...Object.fromEntries([61, 62, 63, 64, 65, 66].map(id => [`GET /api/v1/printers/${id}`, () => new Promise(() => {})])),
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);
    await user.click(await screen.findByRole('button', { name: 'Add 6 printers' }));
    await waitFor(() => expect(posts(calls)).toHaveLength(6));                                // polls never answer, creates still all go out
    await waitFor(() => expect(screen.getByRole('button', { name: 'Scan' })).toBeEnabled());
  });

  it('never sends a blank quick-add value', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(BAMBU), 'GET /api/v1/printers/types': TYPES,
      'POST /api/v1/printers': saved(9, { configured: false }),
      'GET /api/v1/printers/9': saved(9, { configured: false, config_error: 'Missing Access Code' }),
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);
    await user.click(await screen.findByRole('button', { name: 'Add 192.168.7.20' }));

    await waitFor(() => expect(within(rowOf('192.168.7.20')).getByTestId('row-status').textContent)
      .toBe('Added · not configured: Missing Access Code'));
    expect(posts(calls)[0].body).toMatchObject({ connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111' } });
    expect((posts(calls)[0].body as { connection_config: object }).connection_config).not.toHaveProperty('access_code');
    expect(within(rowOf('192.168.7.20')).getByRole('button', { name: 'Save 192.168.7.20' })).toBeTruthy();   // finish setup in place
  });

  it('an IP-conflict row starts unchecked, "All new" skips it, and "Edit <name>" moves the existing printer', async () => {
    const user = userEvent.setup();
    const conflict = found({ ...BAMBU, ip_in_use_by: { printer_id: 7, name: 'A1-Bay4', model: 'A1' } });
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(conflict, ELEGOO('192.168.7.40')), 'GET /api/v1/printers/types': TYPES,
      'GET /api/v1/printers/7': saved(7, { connection_config: { ip_address: '192.168.7.20', access_code: 'secret' } }),
      'PATCH /api/v1/printers/7': saved(7),
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);

    const row = rowOf('192.168.7.20');
    expect(within(row).getByRole('note').textContent).toMatch(/192.168.7.20 is already used by A1-Bay4 \(A1\) — the address may have changed/);
    expect((within(row).getByLabelText('Select 192.168.7.20') as HTMLInputElement).checked).toBe(false);
    await user.click(screen.getByRole('button', { name: 'None' }));
    await user.click(screen.getByRole('button', { name: 'All new' }));
    expect((within(row).getByLabelText('Select 192.168.7.20') as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole('button', { name: 'Add 1 printer' })).toBeTruthy();

    await user.click(within(row).getByRole('button', { name: 'Edit A1-Bay4' }));
    const ip = within(row).getByLabelText('New IP for A1-Bay4');
    await user.clear(ip);
    await user.type(ip, '192.168.7.99');
    await user.click(within(row).getByRole('button', { name: 'Save A1-Bay4' }));
    await waitFor(() => expect(calls.some(c => c.method === 'PATCH')).toBe(true));
    expect(calls.find(c => c.method === 'PATCH')!.body).toEqual({ connection_config: { ip_address: '192.168.7.99', access_code: 'secret' } });
    expect((await within(row).findByRole('status')).textContent).toMatch(/A1-Bay4 is being re-tested at its new address/);
    expect(within(row).getByTestId('row-status').textContent).not.toMatch(/^Added/);          // this row was not added
    expect(within(row).getByRole('button', { name: 'Add 192.168.7.20' })).toBeTruthy();
  });

  it('saving an already-added printer with nothing changed tests it as it is instead', async () => {
    const user = userEvent.setup();
    const added = found({ ...BAMBU, already_added: true,
      added_printer: { printer_id: 5, name: 'Bay 2', configured: false, config_error: "Couldn't reach 192.168.7.20:8883" } });
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(added), 'GET /api/v1/printers/types': TYPES,
      'GET /api/v1/printers/5': saved(5, { connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111', access_code: 'X' } }),
      'POST /api/v1/printers/5/test': { ok: true, error: null, configured: true, config_error: null },
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);
    const section = await screen.findByRole('region', { name: 'Already in your fleet' });
    await user.click(within(section).getByRole('button', { name: 'Edit Bay 2' }));
    await user.click(within(section).getByRole('button', { name: 'Save 192.168.7.20' }));
    await waitFor(() => expect(within(section).getByTestId('row-status').textContent).toBe('Added · configured'));
    expect(calls.some(c => c.method === 'PATCH')).toBe(false);
    expect(calls.filter(c => c.url === '/api/v1/printers/5/test')).toHaveLength(1);
  });

  it('an already-added printer sits in its own section and edits the saved printer by PATCH', async () => {
    const user = userEvent.setup();
    const added = found({ ...BAMBU, already_added: true,
      added_printer: { printer_id: 5, name: 'Bay 2', configured: false, config_error: 'Missing Access Code' } });
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(added), 'GET /api/v1/printers/types': TYPES,
      'GET /api/v1/printers/5': () => calls.some(x => x.method === 'PATCH') ? saved(5)
        : saved(5, { configured: false, connection_config: { ip_address: '192.168.7.20', serial_number: 'S1', use_ams: 1 } }),
      'PATCH /api/v1/printers/5': saved(5, { configured: false }),
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);

    const section = await screen.findByRole('region', { name: 'Already in your fleet' });
    expect(within(section).getByText('Bay 2')).toBeTruthy();
    expect(within(section).getByTestId('row-status').textContent).toBe('Added · not configured: Missing Access Code');
    expect(within(section).queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Add \d/ })).toBeNull();

    await user.click(within(section).getByRole('button', { name: 'Edit Bay 2' }));
    await user.type(within(section).getByLabelText('Access Code for 192.168.7.20'), 'ABCD1234');
    await user.click(within(section).getByRole('button', { name: 'Save 192.168.7.20' }));

    await waitFor(() => expect(within(section).getByTestId('row-status').textContent).toBe('Added · configured'));
    expect(calls.find(c => c.method === 'PATCH')!.body).toEqual({ connection_config: {
      ip_address: '192.168.7.20', serial_number: '01P00A111111111', use_ams: 1, access_code: 'ABCD1234' } });
  });

  it('the model is required when the scan did not match one', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': result(found({ ...BAMBU, model_uuid: null, model: 'Codename C99' })),
      'GET /api/v1/printers/types': TYPES });
    render(<DiscoverPrinters />);
    await openAndScan(user);

    const row = rowOf('192.168.7.20');
    expect(within(row).getByRole('button', { name: 'Add 192.168.7.20' })).toBeDisabled();
    expect(within(row).getByTestId('row-status').textContent).toBe('Missing model');
    expect(screen.getByText(/Choose a model for 192.168.7.20/)).toBeTruthy();
    const model = within(row).getByLabelText('Model for 192.168.7.20') as HTMLInputElement;
    expect([...document.querySelectorAll(`#${model.getAttribute('list')} option`)].map(o => (o as HTMLOptionElement).value))
      .toEqual(['Bambu Lab P1S', 'Bambu Lab A1']);                                          // only the plugin that found it
    await user.type(model, 'Bambu Lab A1');
    expect(within(row).getByRole('button', { name: 'Add 192.168.7.20' })).toBeEnabled();
  });

  it('Test is enabled only when every quick-add field is filled, and editing a field clears its result', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({
      'POST /api/v1/printers/discover': result(BAMBU), 'GET /api/v1/printers/types': TYPES,
      'POST /api/v1/printers/test-connection': { ok: true },
    });
    render(<DiscoverPrinters />);
    await openAndScan(user);

    const row = rowOf('192.168.7.20');
    const test = within(row).getByRole('button', { name: 'Test 192.168.7.20' });
    expect(test).toBeDisabled();
    await user.type(within(row).getByLabelText('Access Code for 192.168.7.20'), 'ABCD');
    await user.click(test);
    await waitFor(() => expect(within(row).getByTestId('row-status').textContent).toBe('Connection OK'));
    expect(calls.find(c => c.url.endsWith('/test-connection'))!.body).toEqual({ printer_type: 'bambu',
      connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111', access_code: 'ABCD' } });
    await user.type(within(row).getByLabelText('Access Code for 192.168.7.20'), 'E');
    expect(within(row).getByTestId('row-status').textContent).toBe('Ready to add');
  });

  it('"More settings…" hands the row, with what was typed, to the full form', async () => {
    const user = userEvent.setup();
    const onMoreSettings = vi.fn();
    stubFetch({ 'POST /api/v1/printers/discover': result(BAMBU), 'GET /api/v1/printers/types': TYPES });
    render(<DiscoverPrinters pluginId="bambu" onMoreSettings={onMoreSettings} />);
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.type(await screen.findByLabelText('Access Code for 192.168.7.20'), 'ABCD');
    await user.click(screen.getByRole('button', { name: 'More settings for 192.168.7.20' }));
    expect(onMoreSettings).toHaveBeenCalledWith(expect.objectContaining({ ip: '192.168.7.20',
      connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111', access_code: 'ABCD' } }));
    expect(window.localStorage.getItem('themis.discovery.ranges')).toBeNull();               // a plugin scan doesn't overwrite it
  });

  it('warns when the scan stopped early and shows a server rejection', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': { ...result(), truncated: true }, 'GET /api/v1/printers/types': TYPES });
    const { unmount } = render(<DiscoverPrinters />);
    await openAndScan(user);
    expect(await screen.findByText(/stopped early; narrow the range/)).toBeTruthy();
    unmount();

    stubFetch({ 'POST /api/v1/printers/discover': new Reply(422, { detail: '8.8.8.0/24 is not a private network' }),
      'GET /api/v1/printers/types': TYPES });
    render(<DiscoverPrinters />);
    await openAndScan(user, '8.8.8.0/24');
    expect((await screen.findByRole('alert')).textContent).toBe('8.8.8.0/24 is not a private network');   // the server's reason, not "422 {…}"
  });

  it('remembers the range for next time', async () => {
    const user = userEvent.setup();
    stubFetch({ 'POST /api/v1/printers/discover': result(), 'GET /api/v1/printers/types': TYPES });
    const first = render(<DiscoverPrinters />);
    await openAndScan(user, '192.168.7.0/24');
    await screen.findByText(/Probed/);
    first.unmount();

    render(<DiscoverPrinters />);
    await user.click(screen.getByRole('button', { name: /scan network/i }));
    expect((screen.getByLabelText('Network ranges') as HTMLInputElement).value).toBe('192.168.7.0/24');
  });
});
