import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LowStockSettings } from './LowStockSettings';
import { Reply, stubFetch } from '../test/fetchStub';
import { mkMaterial } from '../test/inventoryFixtures';

const URL = '/api/v1/inventory/settings';
const MATERIALS = [
  mkMaterial({ ref: '1', name: 'PLA White', vendor: 'Elegoo' }),
  mkMaterial({ ref: '2', name: 'PETG Black', material: 'PETG' }),
];
const settings = (low_stock: { default_g: number | null; overrides: Record<string, number> }) =>
  ({ provider: 'p', deduct_on_complete: true, low_stock });

describe('LowStockSettings', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads the saved default and overrides, naming materials from the inventory', async () => {
    stubFetch({ [`GET ${URL}`]: settings({ default_g: 150, overrides: { '1': 40, '99': 5 } }) });
    render(<LowStockSettings materials={MATERIALS} />);

    expect(((await screen.findByLabelText('Default threshold (g)')) as HTMLInputElement).value).toBe('150');
    expect(within(screen.getByTestId('override-1')).getByText('Elegoo PLA White')).toBeTruthy();
    expect(within(screen.getByTestId('override-1')).getByText('40 g')).toBeTruthy();
    expect(within(screen.getByTestId('override-99')).getByText('Material #99')).toBeTruthy();   // not in the list any more
  });

  it('saves the default and per-filament overrides it was given, and shows what the server stored', async () => {
    const api = stubFetch({
      [`GET ${URL}`]: settings({ default_g: null, overrides: {} }),
      [`PUT ${URL}`]: (c: { body: { low_stock: unknown } }) => settings(c.body.low_stock as never),
    });
    render(<LowStockSettings materials={MATERIALS} />);
    const def = await screen.findByLabelText('Default threshold (g)');
    expect((def as HTMLInputElement).value).toBe('');                       // off by default

    await userEvent.type(def, '200');
    await userEvent.selectOptions(screen.getByLabelText('Material'), 'PETG Black');
    await userEvent.type(screen.getByLabelText('Threshold for material (g)'), '60');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save thresholds' }));

    expect((await screen.findByRole('status')).textContent).toBe('Saved');
    expect(api.to('PUT', URL)[0].body).toEqual({ low_stock: { default_g: 200, overrides: { '2': 60 } } });
    // the added filament is no longer offered again
    expect(within(screen.getByLabelText('Material')).queryByRole('option', { name: 'PETG Black' })).toBeNull();
  });

  it('clearing the default sends null (alerts off) and a removed override is dropped', async () => {
    const api = stubFetch({ [`GET ${URL}`]: settings({ default_g: 100, overrides: { '1': 40 } }), [`PUT ${URL}`]: (c: { body: { low_stock: unknown } }) => settings(c.body.low_stock as never) });
    render(<LowStockSettings materials={MATERIALS} />);
    const def = await screen.findByLabelText('Default threshold (g)');

    await userEvent.clear(def);
    await userEvent.click(screen.getByRole('button', { name: 'Remove threshold for Elegoo PLA White' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save thresholds' }));

    await screen.findByRole('status');
    expect(api.to('PUT', URL)[0].body).toEqual({ low_stock: { default_g: null, overrides: {} } });
  });

  it('shows the API error when saving fails', async () => {
    stubFetch({ [`GET ${URL}`]: settings({ default_g: null, overrides: {} }), [`PUT ${URL}`]: new Reply(422, { detail: 'bad' }) });
    render(<LowStockSettings materials={MATERIALS} />);
    await screen.findByLabelText('Default threshold (g)');

    await userEvent.click(screen.getByRole('button', { name: 'Save thresholds' }));

    expect((await screen.findByRole('alert')).textContent).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('does not add an incomplete or negative override', async () => {
    stubFetch({ [`GET ${URL}`]: settings({ default_g: null, overrides: {} }) });
    render(<LowStockSettings materials={MATERIALS} />);
    await screen.findByLabelText('Default threshold (g)');
    const add = screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement;

    expect(add.disabled).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText('Material'), 'PETG Black');
    expect(add.disabled).toBe(true);                                       // no grams yet
    await userEvent.type(screen.getByLabelText('Threshold for material (g)'), '-5');
    expect(add.disabled).toBe(true);
  });
});
