// The printer-models page's per-model slicer profile (BIZ-294): set once per model, auto-suggested by the slicing provider.
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrinterModelsPanel } from './PrinterModelsPanel';
import type { PrinterModelEntry } from '../api/printers';
import type { ModelSlicerProfile, ProfileSuggestions } from '../api/slicerProfiles';
import { Reply, stubFetch } from '../test/fetchStub';

const MODEL = 'u-p1s';
const MAPPINGS = `/api/v1/printer-models/${MODEL}/slicer-profiles`;
const SUGGEST = `${MAPPINGS}/suggestions?provider_plugin_id=laminus`;
const entry = (over: Partial<PrinterModelEntry> = {}): PrinterModelEntry => ({
  id: MODEL, plugin_id: 'bambu', manufacturer_id: 'bambu', manufacturer_name: 'Bambu Lab', model_id: 'p1s', display_name: 'P1S',
  bed_mm: [256, 256], toolheads: 1, enabled: true, dormant: false, dormant_reason: null, printer_count: 3, slicer_profile_count: 0, ...over,
});
const row = (id: number, d: number, name: string, is_default = false, flow: 'standard' | 'high_flow' = 'standard'): ModelSlicerProfile =>
  ({ id, model_uuid: MODEL, provider_plugin_id: 'laminus', nozzle_diameter_mm: d, flow, machine_profile: name, is_default });
const PLUGINS = { plugins: [{ id: 'laminus', name: 'Laminus', enabled: true, provides: [{ capability: 'gcode.produce' }] }], selections: {} };
const cand = (name: string, d: number, confidence: number) =>
  ({ name, nozzle_diameter_mm: d, flow: 'standard' as const, ref: `machine:${name}`, confidence, match_reason: 'exact_model' });
const suggestions = (over: Partial<ProfileSuggestions> = {}): ProfileSuggestions =>
  ({ provider_plugin_id: 'laminus', available: true, error: null, candidates: [], variants: [], ...over });

/** A fake server: GET lists `rows`; a POST appends `created` (so the component's re-list shows it), like the real one. */
function open(rows: ModelSlicerProfile[], sugg: ProfileSuggestions, extra: Record<string, unknown> = {}, created?: ModelSlicerProfile,
              catalog: unknown[] = []) {
  let current = rows;
  return stubFetch({
    'GET /api/v1/printer-models': [entry({ slicer_profile_count: rows.length })],
    'GET /api/v1/plugins': PLUGINS,
    'GET /api/v1/printers/orca-machine-catalog': catalog,
    [`GET ${MAPPINGS}`]: () => current,
    [`GET ${SUGGEST}`]: sugg,
    [`POST ${MAPPINGS}`]: () => { if (created) current = [...current, created]; return created; },
    ...extra,
  });
}
const expand = async () => userEvent.click(await screen.findByRole('button', { name: 'Slicer profile' }));

afterEach(() => localStorage.clear());

describe('PrinterModelsPanel — slicer profile per model', () => {
  it('flags a model with no profile and one that has some, without opening anything', async () => {
    stubFetch({ 'GET /api/v1/printer-models': [entry(), entry({ id: 'u2', display_name: 'X1C', slicer_profile_count: 2 })] });
    render(<PrinterModelsPanel />);

    expect(await screen.findByText('no slicer profile')).toBeTruthy();
    expect(screen.getByText('2 slicer profiles')).toBeTruthy();
    expect(screen.queryByTestId('model-slicer-profiles')).toBeNull();
  });

  it('auto-suggests from the provider and saves the confirmed profile as the model default, once', async () => {
    const api = open([], suggestions({ candidates: [cand('Bambu Lab P1S 0.4 nozzle', 0.4, 0.98), cand('Bambu Lab P1S 0.6 nozzle', 0.6, 0.9)] }), {},
                     row(1, 0.4, 'Bambu Lab P1S 0.4 nozzle', true));
    render(<PrinterModelsPanel />);
    await expand();

    expect(await screen.findByText(/No slicer profile is set for Bambu Lab P1S yet/)).toBeTruthy();
    expect(screen.getByText('90% match')).toBeTruthy();
    await userEvent.click(screen.getByRole('radio', { name: /Bambu Lab P1S 0\.4 nozzle/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm profile' }));

    await waitFor(() => expect(api.to('POST', MAPPINGS)).toHaveLength(1));
    expect(api.to('POST', MAPPINGS)[0].body).toEqual({
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow: 'standard', machine_profile: 'Bambu Lab P1S 0.4 nozzle', is_default: true });
    expect(await screen.findByTestId('msp-row-1')).toBeTruthy();
    expect(await screen.findByText('1 slicer profile')).toBeTruthy();            // the page's count follows
    expect(screen.queryByRole('button', { name: 'Confirm profile' })).toBeNull(); // confirmed once: no more prompting
  });

  it('offers the other nozzle variants the provider knows and adds one as a non-default', async () => {
    const api = open([row(1, 0.4, 'Bambu Lab P1S 0.4 nozzle', true)], suggestions({ variants: [
      { name: 'Bambu Lab P1S 0.4 nozzle', nozzle_diameter_mm: 0.4, flow: 'standard', ref: 'a', is_default_guess: true },
      { name: 'Bambu Lab P1S 0.6 nozzle', nozzle_diameter_mm: 0.6, flow: 'standard', ref: 'b', is_default_guess: false },
      { name: 'Bambu Lab P1S 0.4 high flow nozzle', nozzle_diameter_mm: 0.4, flow: 'high_flow', ref: 'c', is_default_guess: false },
    ] }), {}, row(2, 0.6, 'Bambu Lab P1S 0.6 nozzle'));
    render(<PrinterModelsPanel />);
    await expand();

    const offered = await screen.findByText('Other nozzle variants of Bambu Lab P1S');
    expect(offered.parentElement!.textContent).not.toContain('Bambu Lab P1S 0.4 nozzle');     // already mapped: not offered again
    const sixRow = screen.getByText('Bambu Lab P1S 0.6 nozzle').parentElement!;
    await userEvent.click(within(sixRow).getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(api.to('POST', MAPPINGS)).toHaveLength(1));
    expect(api.to('POST', MAPPINGS)[0].body).toEqual({
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.6, flow: 'standard', machine_profile: 'Bambu Lab P1S 0.6 nozzle', is_default: false });
  });

  it('makes another variant the default, and removes a mapping', async () => {
    const api = open([row(1, 0.4, 'P1S 0.4', true), row(2, 0.6, 'P1S 0.6')], suggestions(), {
      [`PATCH ${MAPPINGS}/2`]: row(2, 0.6, 'P1S 0.6', true),
      [`DELETE ${MAPPINGS}/2`]: new Reply(204, null),
    });
    render(<PrinterModelsPanel />);
    await expand();

    await userEvent.click(within(await screen.findByTestId('msp-row-2')).getByRole('button', { name: 'Make default' }));
    await waitFor(() => expect(api.to('PATCH', `${MAPPINGS}/2`)).toHaveLength(1));
    expect(api.to('PATCH', `${MAPPINGS}/2`)[0].body).toEqual({ is_default: true });

    await userEvent.click(within(screen.getByTestId('msp-row-2')).getByRole('button', { name: 'Remove 0.6mm' }));
    await waitFor(() => expect(api.to('DELETE', `${MAPPINGS}/2`)).toHaveLength(1));
  });

  it('shows why the server refused a change (e.g. removing the default of a model with other variants)', async () => {
    open([row(1, 0.4, 'P1S 0.4', true), row(2, 0.6, 'P1S 0.6')], suggestions(), {
      [`DELETE ${MAPPINGS}/1`]: new Reply(409, { detail: 'This is the default variant: make another variant the default before removing it' }),
    });
    render(<PrinterModelsPanel />);
    await expand();

    await userEvent.click(within(await screen.findByTestId('msp-row-1')).getByRole('button', { name: 'Remove 0.4mm' }));

    expect((await screen.findByRole('alert')).textContent).toContain('make another variant the default');
    expect(screen.getByTestId('msp-row-1')).toBeTruthy();
  });

  it('works by hand when the provider has no suggestions, and a high-flow nozzle is its own variant', async () => {
    const api = open([row(1, 0.4, 'P1S 0.4', true)], suggestions({ available: false, error: 'profiles returned 404' }), {},
                     row(2, 0.4, 'Bambu Lab P1S 0.4 high flow nozzle', false, 'high_flow'), [
        { name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' },
        { name: 'Bambu Lab P1S 0.4 high flow nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4 HF', source: 'system' },
      ]);
    render(<PrinterModelsPanel />);
    await expand();

    await userEvent.click(await screen.findByText('Add another nozzle variant by hand'));
    await userEvent.selectOptions(await screen.findByLabelText('Make'), 'Bambu Lab');
    await userEvent.selectOptions(screen.getByLabelText('Model'), 'P1S');
    await userEvent.selectOptions(screen.getByLabelText('Nozzle'), '0.4 HF');
    await userEvent.selectOptions(screen.getByLabelText('Flow'), 'high_flow');
    await userEvent.click(screen.getByRole('button', { name: 'Set profile' }));

    await waitFor(() => expect(api.to('POST', MAPPINGS)).toHaveLength(1));
    expect(api.to('POST', MAPPINGS)[0].body).toEqual({
      provider_plugin_id: 'laminus', nozzle_diameter_mm: 0.4, flow: 'high_flow', machine_profile: 'Bambu Lab P1S 0.4 high flow nozzle', is_default: false });
  });

  it('asks the user to enable a slicing provider when none is', async () => {
    open([], suggestions(), { 'GET /api/v1/plugins': { plugins: [], selections: {} } });
    render(<PrinterModelsPanel />);
    await expand();
    expect(await screen.findByText(/No slicing provider is enabled/)).toBeTruthy();
  });
});
