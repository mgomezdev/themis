import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CostsCard } from './CostsCard';
import { Reply, stubFetch } from '../test/fetchStub';
import type { ProjectLabor } from '../api/costs';
import type { ProjectCosts } from '../api/projects';

const URL = '/api/v1/projects/7/labor';
const COSTS: ProjectCosts = { filament: 3, machine: 4, labour: 45, parts: 4, machine_hours: 2, labour_hours: 1.5, total: 56 };
const entry = (id: number, over: Partial<ProjectLabor> = {}): ProjectLabor => ({
  id, project_id: 7, minutes: 90, logged_on: '2026-09-05', note: null, created_at: '', ...over,
});

const show = (costs = COSTS, onChanged = vi.fn()) => render(<CostsCard projectId={7} costs={costs} onChanged={onChanged} />);

describe('CostsCard', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('breaks expenses into filament, machine, labour and parts with a total', async () => {
    stubFetch({ [`GET ${URL}`]: [] });
    show();

    expect(within(screen.getByTestId('cost-filament')).getByText('$3.00')).toBeTruthy();
    const machine = within(screen.getByTestId('cost-machine'));
    expect(machine.getByText('$4.00')).toBeTruthy();
    expect(machine.getByText('2 h of completed prints')).toBeTruthy();
    const labour = within(screen.getByTestId('cost-labour'));
    expect(labour.getByText('$45.00')).toBeTruthy();
    expect(labour.getByText('1.5 h logged')).toBeTruthy();
    expect(within(screen.getByTestId('cost-parts')).getByText('$4.00')).toBeTruthy();
    expect(within(screen.getByTestId('cost-total')).getByText('$56.00')).toBeTruthy();
    expect(await screen.findByText('No labour logged yet.')).toBeTruthy();
  });

  it('lists logged labour with date, duration and note', async () => {
    stubFetch({ [`GET ${URL}`]: [entry(2, { minutes: 45, note: 'post-processing' }), entry(1, { minutes: 120 })] });
    show();

    const row = within(await screen.findByTestId('labor-2'));
    expect(row.getByText('45m')).toBeTruthy();
    expect(row.getByText('post-processing')).toBeTruthy();
    expect(within(screen.getByTestId('labor-1')).getByText('2h')).toBeTruthy();
  });

  it('logs time with a date and note, then refreshes itself and the project', async () => {
    let rows: ProjectLabor[] = [];
    const api = stubFetch({
      [`GET ${URL}`]: () => rows,
      [`POST ${URL}`]: () => { rows = [entry(9, { minutes: 45, note: 'packing', logged_on: '2026-09-10' })]; return rows[0]; },
    });
    const onChanged = vi.fn();
    show(COSTS, onChanged);
    await screen.findByText('No labour logged yet.');

    const add = screen.getByRole('button', { name: 'Log time' }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText('Labour minutes'), '45');
    await userEvent.clear(screen.getByLabelText('Labour date'));
    await userEvent.type(screen.getByLabelText('Labour date'), '2026-09-10');
    await userEvent.type(screen.getByLabelText('Labour note'), ' packing ');
    await userEvent.click(add);

    await screen.findByTestId('labor-9');
    expect(api.to('POST', URL)[0].body).toEqual({ minutes: 45, logged_on: '2026-09-10', note: 'packing' });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText('Labour minutes') as HTMLInputElement).value).toBe('');
  });

  it('only allows whole positive minutes', async () => {
    stubFetch({ [`GET ${URL}`]: [] });
    show();
    await screen.findByText('No labour logged yet.');
    const add = screen.getByRole('button', { name: 'Log time' }) as HTMLButtonElement;

    for (const bad of ['0', '-5', '1.5']) {
      await userEvent.clear(screen.getByLabelText('Labour minutes'));
      await userEvent.type(screen.getByLabelText('Labour minutes'), bad);
      expect(add.disabled, bad).toBe(true);
    }
  });

  it('shows the API error and keeps the input when logging fails', async () => {
    stubFetch({ [`GET ${URL}`]: [], [`POST ${URL}`]: new Reply(422, { detail: 'logged_on can\'t be in the future' }) });
    const onChanged = vi.fn();
    show(COSTS, onChanged);
    await screen.findByText('No labour logged yet.');

    await userEvent.type(screen.getByLabelText('Labour minutes'), '30');
    await userEvent.click(screen.getByRole('button', { name: 'Log time' }));

    expect((await screen.findByRole('alert')).textContent).toBe('logged_on can\'t be in the future');
    expect((screen.getByLabelText('Labour minutes') as HTMLInputElement).value).toBe('30');
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('deletes an entry and refreshes', async () => {
    let rows = [entry(1)];
    const api = stubFetch({ [`GET ${URL}`]: () => rows, [`DELETE ${URL}/1`]: () => { rows = []; return { deleted: 1 }; } });
    const onChanged = vi.fn();
    show(COSTS, onChanged);

    await userEvent.click(await screen.findByRole('button', { name: 'Delete 1h 30m labour entry' }));

    await waitFor(() => expect(screen.queryByTestId('labor-1')).toBeNull());
    expect(api.to('DELETE', `${URL}/1`)).toHaveLength(1);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('tolerates a malformed labour response', async () => {
    stubFetch({ [`GET ${URL}`]: {} });
    show();
    expect(await screen.findByText('No labour logged yet.')).toBeTruthy();
  });
});
