// frontend/src/screens/PrintersScreen.wizard.test.tsx
// The add-printer wizard's slicer-profile step (BIZ-294): the profile belongs to the MODEL, so the step asks for it only when the
// model has none, and the created printer carries no per-printer profile.
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';
import { printerType } from '../test/printerTypes';
import { Reply, stubFetch } from '../test/fetchStub';

const MODEL = 'model-p1s';
const TYPES: PrinterType[] = [
  printerType({ plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S', model_uuid: MODEL, connection_fields: [
    { name: 'ip_address', label: 'IP', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
  ] }),
];
const CATALOG = [
  { name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' },
  { name: 'Bambu Lab P1S 0.6 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.6', source: 'system' },
];
const PLUGINS = { plugins: [{ id: 'laminus', name: 'Laminus', enabled: true, provides: [{ capability: 'gcode.produce' }] }], selections: {} };
const MAPPINGS = `/api/v1/printer-models/${MODEL}/slicer-profiles`;
const mapping = (over = {}) => ({ id: 1, model_uuid: MODEL, provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow: 'standard',
  machine_profile: 'Bambu Lab P1S 0.4 nozzle', is_default: true, ...over });
const SUGGESTIONS = { provider_plugin_id: 'laminus', available: true, error: null, variants: [], candidates: [
  { name: 'Bambu Lab P1S 0.4 nozzle', nozzle_diameter_mm: 0.4, flow: 'standard', ref: 'machine:bambu-lab-p1s-0.4-nozzle', confidence: 0.98, match_reason: 'exact_model' },
  { name: 'Bambu Lab P1S 0.6 nozzle', nozzle_diameter_mm: 0.6, flow: 'standard', ref: 'machine:bambu-lab-p1s-0.6-nozzle', confidence: 0.9, match_reason: 'exact_model' },
] };

function open(routes: Record<string, unknown>) {
  const api = stubFetch({
    'GET /api/v1/printers/orca-machine-catalog': CATALOG,
    'GET /api/v1/plugins': PLUGINS,
    'POST /api/v1/printers': { id: 1 },
    ...routes,
  });
  render(<PrinterAddForm types={TYPES} onCancel={() => {}} onCreated={() => {}} />);
  return api;
}

async function toSlicerStep() {
  await userEvent.click(screen.getByRole('button', { name: /Next/i }));     // Type -> Connect
  await userEvent.click(screen.getByRole('button', { name: /Next/i }));     // Connect -> Slicer profile
}

const printerPost = (api: ReturnType<typeof stubFetch>) => api.to('POST', '/api/v1/printers')[0]?.body as Record<string, unknown>;

afterEach(() => localStorage.clear());

describe('PrinterAddForm slicer profile step', () => {
  it('prompts for the models profile when the model has none, offers the providers suggestion, and the printer is created with no profile of its own', async () => {
    let rows: unknown[] = [];
    const api = open({
      [`GET ${MAPPINGS}`]: () => rows,
      [`GET ${MAPPINGS}/suggestions?provider_plugin_id=laminus`]: SUGGESTIONS,
      [`POST ${MAPPINGS}`]: () => { rows = [mapping()]; return mapping(); },
    });
    await toSlicerStep();

    expect(await screen.findByText(/No slicer profile is set for Bambu Lab P1S yet/)).toBeTruthy();
    await userEvent.click(await screen.findByRole('radio', { name: /Bambu Lab P1S 0\.4 nozzle/ }));
    expect(screen.getByText('98% match')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm profile' }));

    await waitFor(() => expect(api.to('POST', MAPPINGS)).toHaveLength(1));
    expect(api.to('POST', MAPPINGS)[0].body).toEqual({
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow: 'standard', machine_profile: 'Bambu Lab P1S 0.4 nozzle', is_default: true });
    expect(await screen.findByTestId('msp-row-1')).toBeTruthy();                 // now shown as the model's default
    await userEvent.click(screen.getByRole('button', { name: /Next/i }));
    expect(screen.getByText('Bambu Lab P1S 0.4 nozzle')).toBeTruthy();           // review shows the model's profile
    await userEvent.click(screen.getByRole('button', { name: /Finish/i }));

    await waitFor(() => expect(printerPost(api)).toBeTruthy());
    expect(printerPost(api)).not.toHaveProperty('machine_profile_override');
    expect(printerPost(api)).not.toHaveProperty('orca_printer_profiles');
  });

  it('does not prompt when the model already has a profile: it is shown and the user just continues', async () => {
    const api = open({
      [`GET ${MAPPINGS}`]: [mapping()],
      [`GET ${MAPPINGS}/suggestions?provider_plugin_id=laminus`]: SUGGESTIONS,
    });
    await toSlicerStep();

    const row = await screen.findByTestId('msp-row-1');
    expect(within(row).getByText('Bambu Lab P1S 0.4 nozzle')).toBeTruthy();
    expect(screen.queryByText(/No slicer profile is set/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm profile' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Next/i }));
    await userEvent.click(screen.getByRole('button', { name: /Finish/i }));

    await waitFor(() => expect(printerPost(api)).toBeTruthy());
    expect(api.to('POST', MAPPINGS)).toEqual([]);                                // nothing is re-mapped per printer
  });

  it('works without any provider suggestion: the profile is chosen by hand from the catalog', async () => {
    let rows: unknown[] = [];
    const api = open({
      [`GET ${MAPPINGS}`]: () => rows,
      [`GET ${MAPPINGS}/suggestions?provider_plugin_id=laminus`]: { provider_plugin_id: 'laminus', available: false, error: 'profiles returned 404', candidates: [], variants: [] },
      [`POST ${MAPPINGS}`]: () => { rows = [mapping({ nozzle_diameter_mm: 0.6, machine_profile: 'Bambu Lab P1S 0.6 nozzle' })]; return rows[0]; },
    });
    await toSlicerStep();

    expect(await screen.findByText(/Suggestions unavailable \(profiles returned 404\)/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Confirm profile' })).toBeNull();
    await userEvent.click(screen.getByText('Choose a profile by hand'));
    await userEvent.selectOptions(await screen.findByLabelText('Make'), 'Bambu Lab');
    await userEvent.selectOptions(screen.getByLabelText('Model'), 'P1S');
    await userEvent.selectOptions(screen.getByLabelText('Nozzle'), '0.6');
    expect((screen.getByLabelText('Nozzle (mm)') as HTMLInputElement).value).toBe('0.6');      // diameter follows the picked profile
    await userEvent.click(screen.getByRole('button', { name: 'Set profile' }));

    await waitFor(() => expect(api.to('POST', MAPPINGS)).toHaveLength(1));
    expect(api.to('POST', MAPPINGS)[0].body).toEqual({
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.6, flow: 'standard', machine_profile: 'Bambu Lab P1S 0.6 nozzle', is_default: true });
  });

  it('shows why a mapping could not be saved and keeps the prompt', async () => {
    open({
      [`GET ${MAPPINGS}`]: [],
      [`GET ${MAPPINGS}/suggestions?provider_plugin_id=laminus`]: SUGGESTIONS,
      [`POST ${MAPPINGS}`]: new Reply(409, { detail: 'A profile is already mapped for 0.4mm; edit it instead' }),
    });
    await toSlicerStep();
    await userEvent.click(await screen.findByRole('radio', { name: /0\.4 nozzle/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm profile' }));

    expect((await screen.findByRole('alert')).textContent).toContain('already mapped for 0.4mm');
    expect(screen.getByText(/No slicer profile is set/)).toBeTruthy();
  });

  it('says so when no slicing provider is enabled', async () => {
    open({ 'GET /api/v1/plugins': { plugins: [], selections: {} }, [`GET ${MAPPINGS}`]: [] });
    await toSlicerStep();
    expect(await screen.findByText(/No slicing provider is enabled/)).toBeTruthy();
  });
});
