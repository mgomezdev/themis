import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScanSpoolModal } from './ScanSpoolModal';
import { stubFetch } from '../test/fetchStub';

const SPOOL = {
  id: 12, remaining_weight: 412.4, used_weight: 87, location: 'Shelf B · Bin 3',
  filament: { id: 10, vendor: { name: 'ELEGOO' }, name: 'Sky Blue PLA', material: 'PLA', color_hex: '87CEEB' },
};
const FILAMENT = { id: 10, name: 'Sky Blue PLA', material: 'PLA', extra: { orca_profiles: JSON.stringify(JSON.stringify({ 'Bambu X1': ['ELEGOO PLA @X1'] })) } };
const PRINTER = {
  id: 1, name: 'Forge', printer_type: 'bambu', connection_config: {}, awaiting_plate_clear: false, orca_printer_profiles: [],
  current_orca_printer_profile: 'Bambu X1', enabled: true, queue_on: true,
  loaded_filaments: [
    { slot: 0, filament_id: null, name: 'Old PETG', type: 'PETG', color: '#000000' },
    { slot: 1, filament_id: null, name: 'Mid', type: 'PLA', color: '#ffffff', spoolman_spool_id: '3' },
  ],
};
const ROUTES = {
  'GET /api/v1/spoolman/spools': [SPOOL, { ...SPOOL, id: 13, location: null }],
  'GET /api/v1/spoolman/filaments': [FILAMENT],
  'GET /api/v1/printers': [PRINTER],
  'PATCH /api/v1/printers/1': PRINTER,
};

const open = (over: Record<string, unknown> = {}) => {
  const api = stubFetch({ ...ROUTES, ...over });
  const onAssigned = vi.fn(); const onClose = vi.fn();
  render(<ScanSpoolModal onClose={onClose} onAssigned={onAssigned} />);
  return { api, onAssigned, onClose };
};
const enterCode = async (code: string) => {
  const input = await screen.findByLabelText('Spool code');
  await waitFor(() => expect(screen.queryByText('Loading spools…')).toBeNull());
  await userEvent.type(input, code);
};

describe('ScanSpoolModal', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('resolves a typed Spoolman QR payload to the spool, with its location and what is left', async () => {
    open();
    await enterCode('web+spoolman:s-12');

    const card = within(await screen.findByTestId('scanned-spool'));
    expect(card.getByText('#12 ELEGOO Sky Blue PLA · PLA')).toBeTruthy();
    expect(card.getByText('412g left · Stored at Shelf B · Bin 3')).toBeTruthy();
  });

  it('loads the spool into the chosen slot: filament fields copied, spool linked, other slots untouched', async () => {
    const { api, onAssigned } = open();
    await enterCode('12');
    await userEvent.selectOptions(await screen.findByLabelText('Printer'), 'Forge');
    await userEvent.selectOptions(screen.getByLabelText('Slot'), 'T0 — Old PETG');
    await userEvent.click(screen.getByRole('button', { name: 'Load into printer' }));

    await screen.findByRole('status');
    expect(api.to('PATCH', '/api/v1/printers/1')[0].body).toEqual({ loaded_filaments: [
      { slot: 0, filament_id: null, name: 'ELEGOO Sky Blue PLA', type: 'PLA', color: '#87CEEB',
        spoolman_spool_id: '12', filament_profile: 'ELEGOO PLA @X1' },
      PRINTER.loaded_filaments[1],
    ] });
    expect(screen.getByRole('status').textContent).toBe('Loaded ELEGOO Sky Blue PLA into Forge.');
    expect(onAssigned).toHaveBeenCalledTimes(1);
  });

  it('can add the spool as a new slot after the existing ones', async () => {
    const { api } = open();
    await enterCode('s-13');
    await userEvent.selectOptions(await screen.findByLabelText('Printer'), 'Forge');
    await userEvent.click(screen.getByRole('button', { name: 'Load into printer' }));

    await screen.findByRole('status');
    const sent = (api.to('PATCH', '/api/v1/printers/1')[0].body as { loaded_filaments: { slot: number; spoolman_spool_id?: string }[] }).loaded_filaments;
    expect(sent.map(s => s.slot)).toEqual([0, 1, 2]);
    expect(sent[2].spoolman_spool_id).toBe('13');
  });

  it('says so when the code is not a spool code or the spool is unknown, and offers no printer yet', async () => {
    open();
    await enterCode('banana');
    expect(await screen.findByText(/doesn’t look like a Spoolman spool code/)).toBeTruthy();

    await userEvent.clear(screen.getByLabelText('Spool code'));
    await userEvent.type(screen.getByLabelText('Spool code'), '999');
    expect(await screen.findByText('Spool #999 isn’t in Spoolman.')).toBeTruthy();
    expect(screen.queryByLabelText('Printer')).toBeNull();
  });

  it('keeps the dialog open with the error when the printer update fails', async () => {
    const { onAssigned } = open({ 'PATCH /api/v1/printers/1': () => { throw new Error('boom'); } });
    await enterCode('12');
    await userEvent.selectOptions(await screen.findByLabelText('Printer'), 'Forge');
    await userEvent.click(screen.getByRole('button', { name: 'Load into printer' }));

    expect((await screen.findByRole('alert')).textContent).toBeTruthy();
    expect(onAssigned).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('offers the camera only where the browser can scan QR codes', async () => {
    open();
    await screen.findByLabelText('Spool code');
    expect(screen.queryByRole('button', { name: /Scan QR with camera/ })).toBeNull();   // jsdom: no BarcodeDetector
  });
});
