import { describe, expect, it } from 'vitest';
import { fetchModelSlicerProfiles, fetchProfileSuggestions } from './slicerProfiles';
import { stubFetch } from '../test/fetchStub';

// A permissive/partial server reply (an old backend, a stubbed API) must degrade to "nothing mapped / no suggestions", never crash the
// pickers that render these (a thrown render blanks the whole page).
describe('slicer profile API normalisation', () => {
  it('reads a non-list body as no mappings', async () => {
    stubFetch({ 'GET /api/v1/printer-models/m1/slicer-profiles': {} });
    expect(await fetchModelSlicerProfiles('m1')).toEqual([]);
  });

  it('fills a partial suggestions body with safe defaults', async () => {
    stubFetch({ 'GET /api/v1/printer-models/m1/slicer-profiles/suggestions?provider_plugin_id=laminus': {} });
    expect(await fetchProfileSuggestions('m1', 'laminus')).toEqual({
      provider_plugin_id: 'laminus', available: false, error: null, candidates: [], variants: [] });
  });

  it('passes a real suggestions body through', async () => {
    const body = { provider_plugin_id: 'laminus', available: true, error: null, variants: [],
      candidates: [{ name: 'A', nozzle_diameter_mm: 0.4, flow: 'standard', ref: 'r', confidence: 0.9, match_reason: 'exact_model' }] };
    stubFetch({ 'GET /api/v1/printer-models/m1/slicer-profiles/suggestions?provider_plugin_id=laminus': body });
    expect(await fetchProfileSuggestions('m1', 'laminus')).toEqual(body);
  });
});
