import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewJobScreen } from './NewJobScreen';
import { Reply, stubFetch } from '../test/fetchStub';

const PRINTER = {
  id: 1, name: 'Barnabus', printer_type: 'elegoo_centauri', connection_config: {}, awaiting_plate_clear: false,
  orca_printer_profiles: ['Elegoo Centauri Carbon'], current_orca_printer_profile: 'Elegoo Centauri Carbon',
  enabled: true, connected: true, loaded_filaments: [],
};
const plate = (n: number) => ({ plate_number: n, estimated_time: 300, filament_g: 10, thumbnail_path: null });
const PROFILE = '0.20mm Standard @ECC';

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }
const where = () => screen.getByTestId('where').textContent;

function open(plates = [plate(1)], over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/printers': [PRINTER],
    'GET /api/v1/orders': [],
    'GET /api/v1/files': [],
    'GET /api/v1/settings/spoolman': { enabled: false, url: '', has_api_key: false, sync_interval_minutes: 15 },
    'POST /api/v1/files/upload': { id: 42, original_filename: 'model.3mf' },
    'GET /api/v1/files/42/plates': { filename: 'model.3mf', plates },
    'GET /api/v1/files/42/model-filaments': [],
    'GET /api/v1/files/42/embedded-settings': [],
    'GET /api/v1/printers/1/profiles': { print_profiles: [PROFILE], filament_profiles: [] },
    'POST /api/v1/jobs/check-overrides': { has_embedded_settings: false, has_findings: false, setting_changes: [], slot_warning: null },
    'POST /api/v1/jobs': { id: 1, status: 'queued' },
    ...over,
  });
  render(
    <MemoryRouter initialEntries={['/queue/new']}>
      <Where />
      <Routes>
        <Route path="/queue/new" element={<NewJobScreen />} />
        <Route path="/queue" element={<div>QUEUE PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const addButton = () => screen.getByRole('button', { name: /add.*job/i });
async function upload(name = 'model.3mf', user: Pick<ReturnType<typeof userEvent.setup>, 'upload'> = userEvent) {
  await user.upload(fileInput(), new File(['x'], name));
}
/** Pick Barnabus and its print profile for whichever plate tab is showing. */
async function configureActivePlate() {
  await userEvent.click(await screen.findByText('Barnabus'));
  await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
}
const jobPosts = (api: ReturnType<typeof stubFetch>) => api.to('POST', '/api/v1/jobs');

afterEach(() => vi.unstubAllGlobals());

describe('NewJobScreen - upload failures', () => {
  it('shows the server error and leaves the dropzone ready for another try', async () => {
    let attempts = 0;
    const api = open([plate(1)], {
      'POST /api/v1/files/upload': () => (++attempts === 1 ? new Reply(413, 'file too large') : { id: 42, original_filename: 'model.3mf' }),
    });

    await upload('big.3mf');
    expect(await screen.findByText('Upload failed: 413 file too large')).toBeTruthy();
    expect(screen.queryByText('Barnabus')).toBeNull();
    expect(screen.getAllByText(/Drop a \.3mf or \.stl file/i).length).toBeGreaterThan(0);

    await upload('model.3mf');

    expect(await screen.findByText('Barnabus')).toBeTruthy();
    expect(screen.queryByText(/Upload failed/)).toBeNull();          // the error goes away with the next attempt
    expect(api.to('POST', '/api/v1/files/upload')).toHaveLength(2);
  });

  it('rejects other file types before uploading anything', async () => {
    const api = open();

    await upload('notes.txt', userEvent.setup({ applyAccept: false }));

    expect(await screen.findByText('Only .3mf and .stl files are supported.')).toBeTruthy();
    expect(api.to('POST', '/api/v1/files/upload')).toEqual([]);
  });

  it('says so when the uploaded file\'s plates cannot be read', async () => {
    open([plate(1)], { 'GET /api/v1/files/42/plates': new Reply(422, 'not a valid 3mf') });

    await upload();

    expect(await screen.findByText('Upload failed: 422 not a valid 3mf')).toBeTruthy();
    expect(addButton().hasAttribute('disabled')).toBe(true);
  });
});

describe('NewJobScreen - creating the job', () => {
  it('sends the plate, printer and profile, reports success, and offers the queue', async () => {
    const api = open();
    await upload();
    await configureActivePlate();

    await userEvent.click(addButton());

    expect(await screen.findByText(/1 job added to queue/)).toBeTruthy();
    expect(jobPosts(api).map(c => c.body)).toEqual([{
      uploaded_file_id: 42, plate_number: 1, order_id: null, overrides: null,
      printer_configs: [{
        printer_id: 1, print_profile: PROFILE, filament_profile: null, filament_id: null,
        filament_type: 'any', filament_color: 'any', tool_index: null, filament_map: null,
      }],
      model_targets: [],
    }]);
    expect(screen.getAllByText(/Drop a \.3mf or \.stl file/i).length).toBeGreaterThan(0);   // ready for the next file
    await userEvent.click(screen.getByRole('button', { name: 'view queue' }));
    expect(where()).toBe('/queue');
  });

  it('can target any printer of a model instead of a specific printer', async () => {
    const api = open();
    await upload();

    await userEvent.click(await screen.findByTestId('model-target-Elegoo Centauri Carbon'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    await userEvent.click(addButton());

    expect(await screen.findByText(/1 job added to queue/)).toBeTruthy();
    expect(jobPosts(api).map(c => c.body)).toEqual([{
      uploaded_file_id: 42, plate_number: 1, order_id: null, overrides: null,
      printer_configs: [],
      model_targets: [{
        machine_profile: 'Elegoo Centauri Carbon', print_profile: PROFILE, filament_profile: null,
        filament_id: null, filament_type: 'any', filament_color: 'any',
      }],
    }]);
  });

  it('shows why the server refused the job and keeps everything editable for a retry', async () => {
    let attempts = 0;
    const api = open([plate(1)], {
      'POST /api/v1/jobs': () => (++attempts === 1 ? new Reply(422, { detail: 'Printer 1 has no such profile' }) : { id: 1, status: 'queued' }),
    });
    await upload();
    await configureActivePlate();

    await userEvent.click(addButton());

    expect(await screen.findByText('Failed to create job: 422 {"detail":"Printer 1 has no such profile"}')).toBeTruthy();
    expect(screen.queryByText(/added to queue/)).toBeNull();
    expect((screen.getByTestId('print-profile-select') as HTMLSelectElement).value).toBe(PROFILE);   // nothing was reset
    expect(addButton().hasAttribute('disabled')).toBe(false);

    await userEvent.click(addButton());

    expect(await screen.findByText(/1 job added to queue/)).toBeTruthy();
    expect(screen.queryByText(/Failed to create job/)).toBeNull();
    expect(jobPosts(api)).toHaveLength(2);
  });

  it('drops the old error as soon as the retry starts and locks the button while it runs', async () => {
    let attempts = 0;
    let release!: () => void;
    const gate = new Promise<void>(r => { release = r; });
    open([plate(1)], {
      'POST /api/v1/jobs': () => (++attempts === 1 ? new Reply(500, 'disk full') : gate.then(() => ({ id: 1, status: 'queued' }))),
    });
    await upload();
    await configureActivePlate();
    await userEvent.click(addButton());
    await screen.findByText('Failed to create job: 500 disk full');

    await userEvent.click(addButton());

    expect(screen.queryByText(/Failed to create job/)).toBeNull();
    const busy = screen.getByRole('button', { name: /Adding to queue/ });
    expect(busy.hasAttribute('disabled')).toBe(true);
    release();
    expect(await screen.findByText(/1 job added to queue/)).toBeTruthy();
  });

  it('shows a server error (5xx) the same way', async () => {
    open([plate(1)], { 'POST /api/v1/jobs': new Reply(500, 'database is locked') });
    await upload();
    await configureActivePlate();

    await userEvent.click(addButton());

    expect(await screen.findByText('Failed to create job: 500 database is locked')).toBeTruthy();
  });

  it('cannot be submitted until a printer and profile are chosen', async () => {
    const api = open();
    await upload();
    await screen.findByText('Barnabus');
    expect(addButton().hasAttribute('disabled')).toBe(true);

    await userEvent.click(screen.getByText('Barnabus'));
    expect(addButton().hasAttribute('disabled')).toBe(true);          // printer but no profile yet
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);

    expect(addButton().hasAttribute('disabled')).toBe(false);
    expect(jobPosts(api)).toEqual([]);
  });
});

describe('NewJobScreen - several plates', () => {
  async function configureBothPlates() {
    await upload();
    await configureActivePlate();
    await userEvent.click(await screen.findByRole('button', { name: /Plate 2/ }));
    await configureActivePlate();
  }

  it('creates one job per plate, in order', async () => {
    const api = open([plate(1), plate(2)]);
    await configureBothPlates();

    await userEvent.click(addButton());

    expect(await screen.findByText(/2 jobs added to queue/)).toBeTruthy();
    expect(jobPosts(api).map(c => (c.body as { plate_number: number }).plate_number)).toEqual([1, 2]);
  });

  it('does not create the plates that already went through again when the operator retries after a failure', async () => {
    let attempts = 0;
    const api = open([plate(1), plate(2)], {
      // 1st call (plate 1) ok, 2nd call (plate 2) fails, then everything succeeds
      'POST /api/v1/jobs': () => (++attempts === 2 ? new Reply(500, 'disk full') : { id: attempts, status: 'queued' }),
    });
    await configureBothPlates();

    await userEvent.click(addButton());
    expect(await screen.findByText('Failed to create job: 500 disk full (1 of 2 already added; those plates are now skipped)')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Plate 1/ }).textContent).toContain('SKIP');   // plate 1 is already queued
    expect(addButton().hasAttribute('disabled')).toBe(false);
    await userEvent.click(addButton());

    expect(await screen.findByText(/1 job added to queue/)).toBeTruthy();
    const plates = jobPosts(api).map(c => (c.body as { plate_number: number }).plate_number);
    expect(plates).toEqual([1, 2, 2]);                                // plate 1 once; plate 2 failed, then went through
  });
});
