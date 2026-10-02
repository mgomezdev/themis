import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PaymentsCard } from './PaymentsCard';
import { Reply, stubFetch } from '../test/fetchStub';
import type { ProjectPayment } from '../api/payments';

const URL = '/api/v1/projects/7/payments';
const pay = (id: number, over: Partial<ProjectPayment> = {}): ProjectPayment => ({
  id, project_id: 7, amount: 30, received_on: '2026-09-01', method: 'card', note: null, created_at: '', ...over,
});

const renderCard = (onChanged = vi.fn(), price: number | null = 100) =>
  render(<MemoryRouter><PaymentsCard projectId={7} price={price} onChanged={onChanged} /></MemoryRouter>);

describe('PaymentsCard', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists payments with date, method, note and amount, and totals what was received against the price', async () => {
    stubFetch({ [`GET ${URL}`]: [pay(2, { amount: 70, received_on: '2026-09-20', method: 'bank_transfer' }),
                                  pay(1, { note: 'deposit' })] });
    renderCard();

    const row = within(await screen.findByTestId('payment-1'));
    expect(row.getByText('$30.00')).toBeTruthy();
    expect(row.getByText('Card')).toBeTruthy();
    expect(row.getByText('deposit')).toBeTruthy();
    expect(within(screen.getByTestId('payment-2')).getByText('Bank transfer')).toBeTruthy();
    expect(screen.getByTestId('payments-total').textContent).toBe('Received $100.00 of $100.00');
  });

  it('shows an empty state and omits the "of price" part when there is no quoted price', async () => {
    stubFetch({ [`GET ${URL}`]: [] });
    renderCard(vi.fn(), null);

    await screen.findByText('No payments recorded yet.');
    expect(screen.getByTestId('payments-total').textContent).toBe('Received $0.00');
  });

  it('records a payment with its date, method and note, then refreshes itself and the parent', async () => {
    let rows: ProjectPayment[] = [];
    const api = stubFetch({
      [`GET ${URL}`]: () => rows,
      [`POST ${URL}`]: () => { rows = [pay(5, { amount: 25.5, note: 'deposit', method: 'cash', received_on: '2026-09-10' })]; return rows[0]; },
    });
    const onChanged = vi.fn();
    renderCard(onChanged);
    await screen.findByText('No payments recorded yet.');

    const add = screen.getByRole('button', { name: 'Add payment' }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);                                       // no amount yet
    await userEvent.type(screen.getByLabelText('Payment amount'), '25.5');
    await userEvent.clear(screen.getByLabelText('Date received'));
    await userEvent.type(screen.getByLabelText('Date received'), '2026-09-10');
    await userEvent.type(screen.getByLabelText('Payment note'), ' deposit ');
    await userEvent.click(add);

    await screen.findByTestId('payment-5');
    expect(api.to('POST', URL)[0].body).toEqual({ amount: 25.5, received_on: '2026-09-10', method: 'cash', note: 'deposit' });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText('Payment amount') as HTMLInputElement).value).toBe('');   // ready for the next one
  });

  it('shows the API error and keeps the entered amount when recording fails', async () => {
    stubFetch({ [`GET ${URL}`]: [], [`POST ${URL}`]: new Reply(422, { detail: [{ msg: 'Input should be greater than 0' }] }) });
    const onChanged = vi.fn();
    renderCard(onChanged);
    await screen.findByText('No payments recorded yet.');

    await userEvent.type(screen.getByLabelText('Payment amount'), '5');
    await userEvent.click(screen.getByRole('button', { name: 'Add payment' }));

    expect((await screen.findByRole('alert')).textContent).toBe('Input should be greater than 0');
    expect((screen.getByLabelText('Payment amount') as HTMLInputElement).value).toBe('5');
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('deletes a payment only after confirmation', async () => {
    let rows = [pay(1)];
    const api = stubFetch({ [`GET ${URL}`]: () => rows, [`DELETE ${URL}/1`]: () => { rows = []; return { deleted: 1 }; } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const onChanged = vi.fn();
    renderCard(onChanged);
    const del = await screen.findByRole('button', { name: 'Delete payment of $30.00' });

    await userEvent.click(del);
    expect(api.to('DELETE', `${URL}/1`)).toHaveLength(0);                  // cancelled

    await userEvent.click(del);
    await waitFor(() => expect(screen.queryByTestId('payment-1')).toBeNull());
    expect(api.to('DELETE', `${URL}/1`)).toHaveLength(1);
    expect(onChanged).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });
});
