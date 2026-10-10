import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterModelsPanel } from './PrinterModelsPanel';
import type { PrinterModelEntry } from '../api/printers';

const entry = (over: Partial<PrinterModelEntry>): PrinterModelEntry => ({
  id: 'u1', plugin_id: 'acme', manufacturer_id: 'acme', manufacturer_name: 'Acme', model_id: 'x1', display_name: 'X1',
  bed_mm: [256, 256], toolheads: 1, enabled: true, dormant: false, dormant_reason: null, printer_count: 0, ...over,
});

afterEach(() => vi.unstubAllGlobals());

function stub(models: PrinterModelEntry[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (init?.method === 'PATCH') {
      const id = url.split('/').pop()!;
      const body = JSON.parse(String(init.body));
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...models.find(m => m.id === id)!, enabled: body.enabled }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(models) });
  }));
  return calls;
}

describe('PrinterModelsPanel', () => {
  it('lists models grouped by manufacturer and flags a dormant one with its reason and printer count', async () => {
    stub([entry({}), entry({ id: 'u2', manufacturer_name: 'Globex', display_name: 'G1', dormant: true, dormant_reason: 'plugin_removed', printer_count: 2 })]);
    render(<PrinterModelsPanel />);

    expect(await screen.findByLabelText('Acme X1')).toBeChecked();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    expect(screen.getByText(/dormant — plugin removed/)).toBeInTheDocument();
    expect(screen.getByText('2 printers')).toBeInTheDocument();
  });

  it('toggling a model PATCHes its enabled flag by UUID and reflects it', async () => {
    const calls = stub([entry({})]);
    render(<PrinterModelsPanel />);

    await userEvent.click(await screen.findByLabelText('Acme X1'));

    await waitFor(() => expect(screen.getByLabelText('Acme X1')).not.toBeChecked());
    const patch = calls.find(c => c.init?.method === 'PATCH')!;
    expect(patch.url).toBe('/api/v1/printer-models/u1');
    expect(JSON.parse(String(patch.init!.body))).toEqual({ enabled: false });
  });

  it('shows the error and leaves the switch unchanged when the update fails', async () => {
    vi.stubGlobal('fetch', vi.fn((_u: string, init?: RequestInit) => init?.method === 'PATCH'
      ? Promise.resolve({ ok: false, status: 500, statusText: 'boom', text: () => Promise.resolve('boom'), json: () => Promise.resolve({ detail: 'boom' }) })
      : Promise.resolve({ ok: true, json: () => Promise.resolve([entry({})]) })));
    render(<PrinterModelsPanel />);

    await userEvent.click(await screen.findByLabelText('Acme X1'));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByLabelText('Acme X1')).toBeChecked();
  });
});
