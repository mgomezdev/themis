import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HistoryScreen } from './HistoryScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const HISTORY_URL = '/api/v1/jobs/history';

function job(overrides: Record<string, unknown>) {
  return {
    id: 1, uploaded_file_id: 10, plate_number: 1, order_id: null, project_id: null, assigned_printer_id: null,
    status: 'complete', outcome: null, project_item_quantities: null,
    created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T01:00:00', completed_at: '2026-01-01T01:00:00',
    file_name: 'benchy.3mf', printer_name: 'Forge', project_name: null,
    estimate_filament_grams: null, estimate_seconds: null, actual_filament_grams: null, actual_seconds: null,
    ...overrides,
  };
}

const renderScreen = () => render(<MemoryRouter><HistoryScreen /></MemoryRouter>);
const rowOf = (fileName: string) => within(screen.getByText(fileName).closest('tr') as HTMLElement);

describe('HistoryScreen', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows an empty state when there is no finished job', async () => {
    stubFetch({ [`GET ${HISTORY_URL}`]: [] });
    renderScreen();
    await screen.findByText('No completed jobs yet.');
  });

  it('shows the error when history cannot be loaded', async () => {
    stubFetch({ [`GET ${HISTORY_URL}`]: new Reply(500, 'db down') });
    renderScreen();
    await screen.findByText(/500/);
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('renders file, plate suffix, printer, project link and formatted estimates/actuals', async () => {
    stubFetch({
      [`GET ${HISTORY_URL}`]: [
        job({
          id: 1, file_name: 'gear.3mf', plate_number: 2, project_id: 5, project_name: 'Widgets',
          estimate_filament_grams: 12.34, estimate_seconds: 3900, actual_filament_grams: 13, actual_seconds: 3930,
        }),
        job({ id: 2, file_name: 'lid.3mf', printer_name: null, status: 'failed' }),
      ],
    });
    renderScreen();

    await screen.findByText('gear.3mf');
    const gear = rowOf('gear.3mf');
    expect(gear.getByText('p2')).toBeTruthy();
    expect(gear.getByText('Forge')).toBeTruthy();
    expect(gear.getByRole('link', { name: 'Widgets' }).getAttribute('href')).toBe('/projects/5');
    expect(gear.getByText('12.3 g')).toBeTruthy();   // estimate grams, 1 decimal
    expect(gear.getByText('1h 5m')).toBeTruthy();    // 3900 s
    expect(gear.getByText('13.0 g')).toBeTruthy();   // actual grams
    expect(gear.getByText('1h 6m')).toBeTruthy();    // 3930 s rounds to 66 min
    const lid = rowOf('lid.3mf');
    expect(lid.queryByRole('link')).toBeNull();
    expect(lid.queryByText(/^p\d+$/)).toBeNull(); // plate 1 gets no suffix
    expect(lid.getAllByText('—').length).toBeGreaterThanOrEqual(5); // printer, project, 4 numeric cells, outcome
  });

  it('shows the right outcome label or button for each state of a finished project job', async () => {
    const qty = { '11': 2 };
    stubFetch({
      [`GET ${HISTORY_URL}`]: [
        job({ id: 1, file_name: 'awaiting.3mf', project_id: 5, project_item_quantities: qty }),
        job({ id: 2, file_name: 'reviewed.3mf', project_id: 5, project_item_quantities: qty, outcome: 'reviewed' }),
        job({ id: 3, file_name: 'failed.3mf', project_id: 5, project_item_quantities: qty, status: 'failed' }),
        job({ id: 4, file_name: 'loose.3mf' }),
        job({ id: 5, file_name: 'no-qty.3mf', project_id: 5 }),
        job({ id: 6, file_name: 'ok.3mf', project_id: 5, project_item_quantities: qty, outcome: 'assumed_good' }),
        job({ id: 7, file_name: 'bad.3mf', project_id: 5, project_item_quantities: qty, status: 'failed', outcome: 'assumed_failed' }),
      ],
    });
    renderScreen();
    await screen.findByText('awaiting.3mf');

    expect(rowOf('awaiting.3mf').getByRole('button', { name: 'Report results' })).toBeTruthy();
    expect(rowOf('failed.3mf').getByRole('button', { name: 'Report results' })).toBeTruthy();   // a failed plate can be reported too
    expect(rowOf('reviewed.3mf').getByText(/Reviewed/)).toBeTruthy();
    expect(rowOf('ok.3mf').getByText('Assumed OK')).toBeTruthy();
    expect(rowOf('ok.3mf').getByRole('button', { name: 'Change' })).toBeTruthy();
    const bad = rowOf('bad.3mf');
    expect(bad.getByText('Assumed failed').getAttribute('title')).toBe('Cleared without results; its parts are being printed again');
    expect(bad.queryByRole('button')).toBeNull();
    for (const name of ['reviewed.3mf', 'loose.3mf', 'no-qty.3mf']) {
      expect(rowOf(name).queryByRole('button')).toBeNull();
    }
  });

  it('Report results opens the results dialog; saving POSTs the counts, refreshes history and marks the job Reviewed', async () => {
    let reviewed = false;
    const api = stubFetch({
      [`GET ${HISTORY_URL}`]: () => [
        job({ id: 1, file_name: 'a.3mf', project_id: 5, project_item_quantities: { '11': 2 }, outcome: reviewed ? 'reviewed' : null }),
        job({ id: 2, file_name: 'b.3mf', project_id: 5, project_item_quantities: { '11': 1 } }),
      ],
      'GET /api/v1/projects/5': { id: 5, items: [{ id: 11, file_name: 'gear.stl' }, { id: 12, file_name: 'other.stl' }] },
      'POST /api/v1/jobs/1/results': () => { reviewed = true; return { ok: true, requeued_job_ids: [9], held: [] }; },
    });
    renderScreen();
    await screen.findByText('a.3mf');

    await userEvent.click(rowOf('a.3mf').getByRole('button', { name: 'Report results' }));

    await screen.findByRole('dialog', { name: 'Print results' });
    // while the dialog is open no other job can be reported
    expect((rowOf('b.3mf').getByRole('button', { name: 'Report results' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText('other.stl')).toBeNull();   // only parts on this plate
    await userEvent.clear(screen.getByLabelText('Failed gear.stl'));
    await userEvent.type(screen.getByLabelText('Failed gear.stl'), '1');
    await userEvent.click(screen.getByRole('button', { name: 'Save results' }));

    await waitFor(() => expect(api.to('POST', '/api/v1/jobs/1/results')[0].body).toEqual({
      results: [{ project_item_id: 11, quantity_failed: 1 }],
    }));
    await waitFor(() => expect(rowOf('a.3mf').getByText(/Reviewed/)).toBeTruthy());
    expect(api.to('GET', HISTORY_URL).length).toBeGreaterThanOrEqual(2); // refetched after save
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a failed job opens with the whole plate failed; a 409 shows the server message', async () => {
    stubFetch({
      [`GET ${HISTORY_URL}`]: [job({ id: 3, file_name: 'f.3mf', status: 'failed', project_id: 5, project_item_quantities: { '11': 3 } })],
      'GET /api/v1/projects/5': { id: 5, items: [{ id: 11, file_name: 'gear.stl' }] },
      'POST /api/v1/jobs/3/results': new Reply(409, { detail: 'These parts are already being reprinted' }),
    });
    renderScreen();
    await screen.findByText('f.3mf');
    await userEvent.click(rowOf('f.3mf').getByRole('button', { name: 'Report results' }));

    await screen.findByText('This print ended in a failure — adjust if some parts are usable.');
    expect((screen.getByLabelText('Failed gear.stl') as HTMLInputElement).value).toBe('3');
    await userEvent.click(screen.getByRole('button', { name: 'Save results' }));
    await screen.findByText('These parts are already being reprinted');
    expect(screen.getByRole('dialog', { name: 'Print results' })).toBeTruthy();
  });
});
