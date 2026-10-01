import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';
import { stubFetch } from '../test/fetchStub';

const TYPES: PrinterType[] = [
  { printer_type: 'elegoo_centauri', display_name: 'Elegoo Centauri', connection_fields: [
    { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'port', label: 'Port', field_type: 'number', required: false, default: 3030, placeholder: '', help_text: '' },
  ] },
  { printer_type: 'bambu', display_name: 'Bambu Lab', connection_fields: [
    { name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'serial_number', label: 'Serial Number', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null, placeholder: '', help_text: '' },
  ] },
];

const FOUND = {
  ranges: ['192.168.7.0/24'], scanned: 254, truncated: false,
  found: [{
    printer_type: 'bambu', display_name: 'Bambu Lab', ip: '192.168.7.20', model: 'P1S', name: 'Bambu-P1S',
    serial: '01P00A111111111', connection_config: { ip_address: '192.168.7.20', serial_number: '01P00A111111111' },
    note: null, already_added: false,
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

    expect(screen.getByText('Connect to Bambu Lab')).toBeTruthy();                       // step 2 of a Bambu, not the default first type
    const field = (label: string) => screen.getByText(label).parentElement!.querySelector('input') as HTMLInputElement;
    expect(field('IP Address').value).toBe('192.168.7.20');
    expect(field('Serial Number').value).toBe('01P00A111111111');
    expect(field('Access Code').value).toBe('');                                          // secret: never discovered
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
