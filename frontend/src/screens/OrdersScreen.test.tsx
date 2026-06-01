import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { OrdersScreen } from './OrdersScreen';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
class FakeWS { onmessage: ((e: MessageEvent) => void) | null = null; close() {} }
vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);

const ORDER = {
  id: 1, order_type: 'customer', customer: 'Vela Robotics', title: 'Brackets',
  due_date: '2026-06-01', notes: '', on_hold: false,
  parts: [{ id: 'p1', name: 'Arm L', qty: 8, material: 'PA-CF', est_minutes: 78 }],
  status: 'in_progress', progress: 0.5, job_count: 2, created_at: '', updated_at: '',
};

function mockOk(body: unknown) {
  mockFetch.mockResolvedValue({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;
beforeEach(() => vi.clearAllMocks());

describe('OrdersScreen', () => {
  it('renders orders from the api', async () => {
    mockOk([ORDER]);
    render(<OrdersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText('Vela Robotics')).toBeTruthy());
  });

  it('shows empty state with no orders', async () => {
    mockOk([]);
    render(<OrdersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/no orders/i)).toBeTruthy());
  });
});
