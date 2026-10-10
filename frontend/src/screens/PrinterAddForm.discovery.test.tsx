import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';
import { stubFetch } from '../test/fetchStub';
import { printerType } from '../test/printerTypes';

const TYPES: PrinterType[] = [
  printerType({ plugin_id: 'elegoo_centauri', manufacturer_id: 'elegoo', manufacturer_name: 'Elegoo', model_id: 'centauri', display_name: 'Centauri Carbon', connection_fields: [
    { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'port', label: 'Port', field_type: 'number', required: false, default: 3030, placeholder: '', help_text: '' },
  ] }),
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S', connection_fields: [
    { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'serial_number', label: 'Serial Number', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null, placeholder: '', help_text: '' },
  ] }),
];

const FOUND = {
  ranges: ['192.168.7.0/24'], scanned: 254, truncated: false,
  found: [{
    printer_type: 'bambu', plugin_id: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.20', model: 'P1S', name: 'Bambu-P1S',
    serial: '01P00A111111111', connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111' },
    note: null, already_added: false, model_uuid: null,
  }],
};

afterEach(() => vi.unstubAllGlobals());

describe('PrinterAddForm — discovery', () => {
  it('choosing a discovered printer pre-fills type, nickname and connection fields and jumps to the connect step, leaving only secrets blank', async () => {
    const user = userEvent.setup();
    stubFetch({ 'GET /api/v1/printers/orca-machine-catalog': [], 'POST /api/v1/printers/discover': FOUND });
    render(<PrinterAddForm types={TYPES} onCancel={() => {}} onCreated={() => {}} />);

    await user.click(screen.getByRole('button', { name: /scan network for printers/i }));
    await user.type(screen.getByLabelText('Network ranges'), '192.168.7.0/24');
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));

    expect(screen.getByText('Connect to P1S')).toBeTruthy();                       // step 2 of a Bambu, not the default first type
    const field = (label: string) => screen.getByText(label).parentElement!.querySelector('input') as HTMLInputElement;
    expect(field('IP Address').value).toBe('192.168.7.20');
    expect(field('Serial Number').value).toBe('01P00A111111111');
    expect(field('Access Code').value).toBe('');                                          // secret: never discovered
  });

  it("prefers the server's registry match (model_uuid) over name guessing when the announced text names no declared model", async () => {
    const user = userEvent.setup();
    const types = [TYPES[0], { ...TYPES[1], model_uuid: 'uuid-p1s' }, printerType({ ...TYPES[1], model_id: 'x1c', display_name: 'X1 Carbon', model_uuid: 'uuid-x1c' })];
    const found = { ...FOUND, found: [{ ...FOUND.found[0], model: 'Codename C12', model_uuid: 'uuid-x1c' }] };
    stubFetch({ 'GET /api/v1/printers/orca-machine-catalog': [], 'POST /api/v1/printers/discover': found });
    render(<PrinterAddForm types={types} onCancel={() => {}} onCreated={() => {}} />);

    await user.click(screen.getByRole('button', { name: /scan network for printers/i }));
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));

    expect(screen.getByText('Connect to X1 Carbon')).toBeTruthy();
  });

  it('keeps a nickname the user already typed', async () => {
    const user = userEvent.setup();
    const { calls } = stubFetch({ 'GET /api/v1/printers/orca-machine-catalog': [], 'POST /api/v1/printers/discover': FOUND });
    render(<PrinterAddForm types={TYPES} onCancel={() => {}} onCreated={() => {}} />);
    await user.type(screen.getByPlaceholderText('e.g. Atlas, Forge, Iris'), 'My Atlas');

    await user.click(screen.getByRole('button', { name: /scan network for printers/i }));
    await user.click(screen.getByRole('button', { name: 'Scan' }));
    await user.click(await screen.findByRole('button', { name: 'Use 192.168.7.20' }));
    await user.click(screen.getByRole('button', { name: /^Next/ }));                     // → profile step
    await user.click(screen.getByRole('button', { name: /^Next/ }));                     // → review
    expect(await screen.findByText('My Atlas')).toBeTruthy();
    expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/printers'))).toBe(false);   // nothing created yet
  });
});
