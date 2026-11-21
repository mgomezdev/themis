import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { stubFetch, Reply } from '../test/fetchStub';
import { PrinterTopologyPanel } from './PrinterTopologyPanel';

const topology = { tools: [{ id: 'T0', nozzle_mm: .6 }], positions: [{ id: 'a', filament: null }, { id: 'b', filament: { type: 'PLA' } }], swapping: true };
describe('live printer topology', () => {
  it('updates nozzle sizes and capacity through refresh without reconnecting', async () => {
    const { calls } = stubFetch({ 'POST /api/v1/printers/7/topology/refresh': { topology, revision: 2 } });
    render(<PrinterTopologyPanel printerId={7} />);
    fireEvent.change(screen.getByLabelText('Installed nozzle sizes (mm)'), { target: { value: '0.6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh topology' }));
    await screen.findByText('2 configured positions · 1 loaded');
    expect(screen.getByText('T0: 0.6 mm')).toBeInTheDocument();
    expect(calls.map(c => c.url)).toEqual(['/api/v1/printers/7/topology/refresh']);
    expect(calls[0].body).toEqual({ configuration: { nozzle_sizes_mm: [.6] } });
  });
  it('reports refresh failure without replacing the last display', async () => {
    stubFetch({ 'POST /api/v1/printers/7/topology/refresh': new Reply(502, { detail: 'Refresh failed' }) });
    render(<PrinterTopologyPanel printerId={7} initial={topology} />);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh topology' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Refresh failed'));
    expect(screen.getByText('T0: 0.6 mm')).toBeInTheDocument();
  });
});
