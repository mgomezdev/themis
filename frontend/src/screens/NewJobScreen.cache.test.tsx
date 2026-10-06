import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewJobScreen } from './NewJobScreen';
import { Reply, stubFetch } from '../test/fetchStub';

// New Job and the slicing cache (BIZ-194): the "save sliced gcode" option and the "use a cached version or reslice"
// prompt.

const MACHINE = 'Elegoo Centauri Carbon';
const PRINTER = {
  id: 1, name: 'Barnabus', printer_type: 'elegoo_centauri', connection_config: {}, awaiting_plate_clear: false,
  orca_printer_profiles: [MACHINE], current_orca_printer_profile: MACHINE, enabled: true, connected: true,
  loaded_filaments: [],
};
const plate = (n: number) => ({ plate_number: n, estimated_time: 300, filament_g: 10, thumbnail_path: null });
const PROFILE = '0.20mm Standard @ECC';
const version = (over: Record<string, unknown> = {}) => ({
  id: 5, file_id: 77, name: 'model - PETG - 0.20mm - ECC.gcode', kind: 'gcode', plate_number: 1,
  machine_preset: MACHINE, process_preset: PROFILE, filament_presets: ['Generic PETG'], filament_type: 'PETG',
  filament_color: '#112233', bed_type: null, overrides: {}, estimated_seconds: 3600, filament_grams: 12.5,
  created_at: '2026-10-01T00:00:00Z', source_changed: false, stale: false, stale_reasons: [], printable_now: true,
  ...over,
});

function open(versions: unknown[] = [], over: Record<string, unknown> = {}) {
  const api = stubFetch({
    'GET /api/v1/printers': [PRINTER],
    'GET /api/v1/projects': [],
    'GET /api/v1/files': [],
    'GET /api/v1/plugins': { plugins: [], slots: {} },
    'GET /api/v1/settings/queue': { slice_cache_use_latest_settings: true },
    'POST /api/v1/files/upload': { id: 42, original_filename: 'model.3mf', folder: '/Prints' },
    'GET /api/v1/files/42/plates': { filename: 'model.3mf', plates: [plate(1)] },
    'GET /api/v1/files/42/model-filaments': [],
    'GET /api/v1/files/42/embedded-settings': [],
    'GET /api/v1/files/42/sliced-versions?plate=1': versions,
    'GET /api/v1/files/77/plates': { filename: 'cached.gcode', plates: [plate(1)] },
    'GET /api/v1/printers/1/profiles': { print_profiles: [PROFILE], filament_profiles: [] },
    'POST /api/v1/jobs/check-overrides': { has_embedded_settings: false, has_findings: false, setting_changes: [], slot_warning: null },
    'POST /api/v1/jobs': { id: 1, status: 'queued' },
    ...over,
  });
  render(
    <MemoryRouter initialEntries={['/queue/new']}>
      <Routes><Route path="/queue/new" element={<NewJobScreen />} /></Routes>
    </MemoryRouter>,
  );
  return api;
}

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const addButton = () => screen.getByRole('button', { name: /add.*job/i });
const upload = (name = 'model.3mf') => userEvent.upload(fileInput(), new File(['x'], name));
const jobPosts = (api: ReturnType<typeof stubFetch>) => api.to('POST', '/api/v1/jobs').map(c => c.body);

afterEach(() => vi.unstubAllGlobals());

describe('NewJobScreen — cached sliced versions', () => {
  it('asks nothing when the model has no cached versions', async () => {
    const api = open([]);
    await upload();
    await screen.findByText('Barnabus');
    expect(api.to('GET', '/api/v1/files/42/sliced-versions?plate=1')).toHaveLength(1);
    expect(screen.queryByRole('region', { name: 'Cached sliced versions' })).toBeNull();
  });

  it('offers the versions with their warnings', async () => {
    open([
      version(),
      version({ id: 6, name: 'old.gcode', stale: true, stale_reasons: ['presets_changed', 'slicer_version_changed'],
                source_changed: true, printable_now: false }),
    ]);
    await upload();

    const prompt = await screen.findByRole('region', { name: 'Cached sliced versions' });
    const rows = within(prompt).getAllByTestId('cached-version');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).queryByText(/Stale/)).toBeNull();
    expect(within(rows[0]).getByText(`${MACHINE} · ${PROFILE} · PETG`, { exact: false })).toBeTruthy();
    for (const chip of ['Model changed since slicing', 'Stale: presets edited', 'Stale: OrcaSlicer updated', 'No matching printer']) {
      expect(within(rows[1]).getByText(chip)).toBeTruthy();
    }
    expect(within(rows[1]).getByText('Automatic reuse would reslice this.')).toBeTruthy();
  });

  it('drops the "would reslice" note when stale gcode is pinned', async () => {
    open([version({ stale: true, stale_reasons: ['presets_changed'] })],
         { 'GET /api/v1/settings/queue': { slice_cache_use_latest_settings: false } });
    await upload();
    const prompt = await screen.findByRole('region', { name: 'Cached sliced versions' });
    expect(within(prompt).getByText('Stale: presets edited')).toBeTruthy();
    await act(async () => {});   // the settings fetch has settled
    expect(within(prompt).queryByText('Automatic reuse would reslice this.')).toBeNull();
  });

  it('"Use this version" queues the cached file for that printer model with its filament, no profile or overrides', async () => {
    localStorage.clear();
    const api = open([version()]);
    await upload();
    await userEvent.click(within(await screen.findByRole('region', { name: 'Cached sliced versions' })).getByRole('button', { name: 'Use this version' }));

    expect(await screen.findByTestId('using-cached')).toBeTruthy();
    expect(screen.getByTestId('gcode-warning')).toBeTruthy();
    expect(screen.queryByText('Save sliced gcode to library')).toBeNull();   // nothing new to save
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    expect(jobPosts(api)).toEqual([{
      uploaded_file_id: 77, plate_number: 1, project_id: null, overrides: null, printer_configs: [],
      model_targets: [{
        machine_profile: MACHINE, print_profile: '', filament_profile: null, filament_id: null, material_provider: null, material_ref: null,
        filament_type: 'PETG', filament_color: '#112233',
      }],
    }]);
  });

  it('"Reslice instead" goes back to the model and doesn\'t ask again', async () => {
    const api = open([version()]);
    await upload();
    await userEvent.click(within(await screen.findByRole('region', { name: 'Cached sliced versions' })).getByRole('button', { name: 'Use this version' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Reslice instead' }));

    await userEvent.click(await screen.findByText('Barnabus'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    expect(screen.queryByRole('region', { name: 'Cached sliced versions' })).toBeNull();
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    expect((jobPosts(api)[0] as { uploaded_file_id: number }).uploaded_file_id).toBe(42);
  });

  it('"Reslice" dismisses the prompt and the job slices as usual', async () => {
    const api = open([version()]);
    await upload();
    await userEvent.click(within(await screen.findByRole('region', { name: 'Cached sliced versions' })).getByRole('button', { name: 'Reslice' }));

    expect(screen.queryByRole('region', { name: 'Cached sliced versions' })).toBeNull();
    await userEvent.click(await screen.findByText('Barnabus'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    const body = jobPosts(api)[0] as { uploaded_file_id: number; printer_configs: { print_profile: string }[] };
    expect(body.uploaded_file_id).toBe(42);
    expect(body.printer_configs[0].print_profile).toBe(PROFILE);
  });
});

describe('NewJobScreen — cached versions, edge cases', () => {
  it('a save choice made for the model is not sent with a cached version', async () => {
    const api = open([version()]);
    await upload();
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Save sliced gcode to library' }));
    await userEvent.click(within(await screen.findByRole('region', { name: 'Cached sliced versions' })).getByRole('button', { name: 'Use this version' }));
    await screen.findByTestId('using-cached');
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    expect(jobPosts(api)[0]).not.toHaveProperty('save_slice');
  });

  it('if the cached file fails to load, the form goes back to the model', async () => {
    const api = open([version()], { 'GET /api/v1/files/77/plates': new Reply(500, 'boom') });
    await upload();
    await userEvent.click(within(await screen.findByRole('region', { name: 'Cached sliced versions' })).getByRole('button', { name: 'Use this version' }));

    expect(await screen.findByText(/Failed to load the cached version/)).toBeTruthy();
    expect(screen.queryByTestId('using-cached')).toBeNull();
    await userEvent.click(await screen.findByText('Barnabus'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    await userEvent.click(addButton());
    await screen.findByText(/1 job added to queue/);
    expect((jobPosts(api)[0] as { uploaded_file_id: number }).uploaded_file_id).toBe(42);
  });

  it('warns that only that plate is queued when the model has several', async () => {
    open([version()], { 'GET /api/v1/files/42/plates': { filename: 'model.3mf', plates: [plate(1), plate(2)] },
                        'GET /api/v1/files/42/sliced-versions?plate=2': [] });
    await upload();
    const prompt = await screen.findByRole('region', { name: 'Cached sliced versions' });
    expect(within(prompt).getByText(/the other plates of this model aren.t queued/)).toBeTruthy();
  });
});

describe('NewJobScreen — save sliced gcode', () => {
  it('sends save_slice and the trimmed name when ticked', async () => {
    const api = open();
    await upload();
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Save sliced gcode to library' }));
    expect(screen.getByText('Saved next to model.3mf in /Prints — reuse it next time instead of slicing again.')).toBeTruthy();
    await userEvent.type(screen.getByLabelText('Saved gcode name'), '  Benchy PETG  ');
    await userEvent.click(await screen.findByText('Barnabus'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    expect(jobPosts(api)[0]).toMatchObject({ save_slice: true, save_slice_name: 'Benchy PETG' });
  });

  it('sends nothing about saving when left off', async () => {
    const api = open();
    await upload();
    await userEvent.click(await screen.findByText('Barnabus'));
    await userEvent.selectOptions(await screen.findByTestId('print-profile-select'), PROFILE);
    await userEvent.click(addButton());

    await screen.findByText(/1 job added to queue/);
    expect(jobPosts(api)[0]).not.toHaveProperty('save_slice');
  });

  it('is not offered for a pre-sliced file', async () => {
    open([], {
      'POST /api/v1/files/upload': { id: 42, original_filename: 'part.gcode' },
      'GET /api/v1/files/42/plates': { filename: 'part.gcode', plates: [plate(1)] },
    });
    await upload('part.gcode');
    await screen.findByText('Barnabus');
    expect(screen.queryByText('Save sliced gcode to library')).toBeNull();
  });
});
