import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Printer } from '../data/types';
import { stubFetch } from '../test/fetchStub';
import { IP_FIELD, printerType } from '../test/printerTypes';
import { QueueStateBadges, SetupIncompleteCard } from './FleetSetup';

const printer = (over: Partial<Printer>): Printer => ({
  id: '4', name: 'P1S', nickname: 'Bay 4', model: 'bambu', badge: 'P1S', buildVolume: '', capabilities: [], chamber: false,
  status: 'offline', progress: 0, timeRemaining: 0, timeElapsed: 0, layer: null, nozzleTemp: 0, bedTemp: 0, chamberTemp: null,
  material: { name: '—', type: '—', color: '#475472' }, currentJobId: null, accent: '#888', fanModel: 0, fanAux: 0, fanBox: 0,
  bedTempTarget: 0, queueOn: true, configured: true, configError: null, awaitingPlateClear: false, noSnapshotsWhileIdle: false,
  ...over,
});

const P1S = printerType({
  plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s', display_name: 'P1S',
  connection_fields: [IP_FIELD, { ...IP_FIELD, name: 'serial_number', label: 'Serial Number', quick_add: true },
    { ...IP_FIELD, name: 'access_code', label: 'Access Code', field_type: 'password', quick_add: true },
    { ...IP_FIELD, name: 'use_ams', label: 'Use AMS', required: false }],
});
const STORED = { id: 4, plugin_id: 'bambu', manufacturer_id: 'bambu', model_id: 'p1s', configured: false, config_error: 'Missing Access Code',
  connection_config: { ip_address: '10.0.0.4', serial_number: 'S4', use_ams: 1 } };

afterEach(() => vi.unstubAllGlobals());

describe('Fleet: out-of-queue states (BIZ-369)', () => {
  it('"Queue off" and "Setup incomplete" are distinct, setup first when both hold', () => {
    const { unmount } = render(<QueueStateBadges printer={printer({ queueOn: false })} />);
    expect(screen.getByText('QUEUE OFF')).toBeTruthy();
    expect(screen.queryByText('SETUP INCOMPLETE')).toBeNull();
    unmount();

    const b = render(<QueueStateBadges printer={printer({ configured: false, configError: 'Missing Access Code' })} />);
    expect(screen.getByText('SETUP INCOMPLETE').getAttribute('title')).toBe('Missing Access Code');
    expect(screen.queryByText('QUEUE OFF')).toBeNull();
    b.unmount();

    const { container } = render(<QueueStateBadges printer={printer({ configured: false, queueOn: false })} />);
    expect([...container.querySelectorAll('span')].map(s => s.textContent)).toEqual(['SETUP INCOMPLETE', 'QUEUE OFF']);
  });

  it('a configured printer (or an older server without the field) shows no setup card', () => {
    const { container } = render(<SetupIncompleteCard printer={printer({ configured: undefined })} refetchFleet={() => {}} />);
    expect(container.innerHTML).toBe('');
  });

  it('the setup card shows the reason and Test calls POST /printers/{id}/test then refetches', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const { to } = stubFetch({ 'POST /api/v1/printers/4/test': { ok: false, error: "Couldn't reach 10.0.0.4:8883", configured: false,
      config_error: "Couldn't reach 10.0.0.4:8883" } });
    render(<SetupIncompleteCard printer={printer({ configured: false, configError: 'Missing Access Code' })} refetchFleet={refetch} />);
    expect(screen.getByTestId('setup-card').textContent).toMatch(/Setup incomplete — Missing Access Code/);

    await user.click(screen.getByRole('button', { name: 'Test' }));
    await waitFor(() => expect(refetch).toHaveBeenCalled());
    expect(to('POST', '/api/v1/printers/4/test')).toHaveLength(1);
    expect(screen.getByRole('alert').textContent).toBe("Couldn't reach 10.0.0.4:8883");
  });

  it('Finish setup shows the IP and quick-add fields; Save & connect PATCHes them over the stored config', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const { to } = stubFetch({ 'GET /api/v1/printers/4': STORED, 'GET /api/v1/printers/types': [P1S],
      'PATCH /api/v1/printers/4': { ...STORED, configured: false, config_error: null } });
    render(<SetupIncompleteCard printer={printer({ configured: false })} refetchFleet={refetch} />);

    await user.click(screen.getByRole('button', { name: 'Finish setup' }));
    expect((await screen.findByLabelText('IP Address for Bay 4') as HTMLInputElement).value).toBe('10.0.0.4');
    expect((screen.getByLabelText('Serial Number for Bay 4') as HTMLInputElement).value).toBe('S4');
    expect(screen.queryByLabelText('Use AMS for Bay 4')).toBeNull();                         // not a quick-add field
    await user.type(screen.getByLabelText('Access Code for Bay 4'), 'ABCD1234');
    await user.click(screen.getByRole('button', { name: 'Save & connect' }));

    await waitFor(() => expect(to('PATCH', '/api/v1/printers/4')).toHaveLength(1));
    expect(to('PATCH', '/api/v1/printers/4')[0].body).toEqual({ connection_config: {
      ip_address: '10.0.0.4', serial_number: 'S4', use_ams: 1, access_code: 'ABCD1234' } });
    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });
});
