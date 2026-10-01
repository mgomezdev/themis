import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CostSettings } from './CostSettings';
import { Reply, stubFetch } from '../test/fetchStub';

const URL = '/api/v1/settings/costs';

describe('CostSettings', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads the saved rates and explains that they apply live to past jobs', async () => {
    stubFetch({ [`GET ${URL}`]: { machine_rate_per_hour: 1.5, labour_rate_per_hour: 25 } });
    render(<CostSettings />);

    expect(((await screen.findByLabelText('Machine rate per hour')) as HTMLInputElement).value).toBe('1.5');
    expect((screen.getByLabelText('Labour rate per hour') as HTMLInputElement).value).toBe('25');
    expect(screen.getByText(/re-prices past jobs/)).toBeTruthy();
  });

  it('saves both rates and shows what the server stored', async () => {
    const api = stubFetch({
      [`GET ${URL}`]: { machine_rate_per_hour: 0, labour_rate_per_hour: 0 },
      [`PUT ${URL}`]: (c: { body: unknown }) => c.body,
    });
    render(<CostSettings />);
    const machine = await screen.findByLabelText('Machine rate per hour');

    await userEvent.clear(machine); await userEvent.type(machine, '2.5');
    await userEvent.clear(screen.getByLabelText('Labour rate per hour')); await userEvent.type(screen.getByLabelText('Labour rate per hour'), '30');
    await userEvent.click(screen.getByRole('button', { name: 'Save rates' }));

    expect((await screen.findByRole('status')).textContent).toBe('Saved');
    expect(api.to('PUT', URL)[0].body).toEqual({ machine_rate_per_hour: 2.5, labour_rate_per_hour: 30 });
  });

  it('will not save a blank or negative rate', async () => {
    stubFetch({ [`GET ${URL}`]: { machine_rate_per_hour: 1, labour_rate_per_hour: 1 } });
    render(<CostSettings />);
    const machine = await screen.findByLabelText('Machine rate per hour');
    const save = screen.getByRole('button', { name: 'Save rates' }) as HTMLButtonElement;

    await userEvent.clear(machine);
    expect(save.disabled).toBe(true);
    await userEvent.type(machine, '-3');
    expect(save.disabled).toBe(true);
    await userEvent.clear(machine); await userEvent.type(machine, '0');
    expect(save.disabled).toBe(false);                      // zero is a legitimate rate
  });

  it('shows the API error when saving fails', async () => {
    stubFetch({ [`GET ${URL}`]: { machine_rate_per_hour: 1, labour_rate_per_hour: 1 }, [`PUT ${URL}`]: new Reply(403, { detail: 'API key lacks required scope: settings:write' }) });
    render(<CostSettings />);
    await screen.findByLabelText('Machine rate per hour');

    await userEvent.click(screen.getByRole('button', { name: 'Save rates' }));

    expect((await screen.findByRole('alert')).textContent).toBe('API key lacks required scope: settings:write');
  });
});
