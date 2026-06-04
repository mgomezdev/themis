// frontend/src/screens/PrintersScreen.wizard.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';

const TYPES: PrinterType[] = [
  { printer_type: 'bambu', display_name: 'Bambu', connection_fields: [
    { name: 'ip_address', label: 'IP', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
  ] },
];
const CATALOG = [{ name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/orca-machine-catalog')) return new Response(JSON.stringify(CATALOG), { status: 200 });
    if (url === '/api/v1/printers' && init?.method === 'POST')
      return new Response(JSON.stringify({ id: 1 }), { status: 201 });
    return new Response('[]', { status: 200 });
  }));
});

describe('PrinterAddForm profile step', () => {
  it('sends current_orca_printer_profile on finish', async () => {
    const fetchMock = vi.mocked(fetch);
    render(<PrinterAddForm types={TYPES} onCancel={() => {}} onCreated={() => {}} />);
    // Step 1 → 2
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // Step 2 (Connect) → 3 (Profile)
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // pick make/model/nozzle
    await waitFor(() => screen.getByLabelText('Make'));
    fireEvent.change(screen.getByLabelText('Make'), { target: { value: 'Bambu Lab' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'P1S' } });
    fireEvent.change(screen.getByLabelText('Nozzle'), { target: { value: '0.4' } });
    // Profile → Review
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // Finish
    fireEvent.click(screen.getByRole('button', { name: /Finish/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(c => c[0] === '/api/v1/printers' && (c[1] as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body.current_orca_printer_profile).toBe('Bambu Lab P1S 0.4 nozzle');
    });
  });
});
