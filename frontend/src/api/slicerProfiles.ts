// Model -> slicer machine profile mapping (BIZ-294): the machine profile is set once per printer model (per slicing provider and
// nozzle variant), not per printer. Suggestions come from the slicing provider when it can offer them; manual entry never needs it.
import { apiFetch } from './client';
import type { NozzleVariant } from './printers';

export interface ModelSlicerProfile {
  id: number;
  model_uuid: string;
  provider_plugin_id: string;
  nozzle_diameter_mm: number;
  flow: 'standard' | 'high_flow';
  machine_profile: string;
  is_default: boolean;
}

export interface ProfileCandidate {
  name: string;
  nozzle_diameter_mm: number | null;
  flow: 'standard' | 'high_flow';
  ref: string;
  confidence: number;
  match_reason: string;
}

export interface ProfileVariantOption {
  name: string;
  nozzle_diameter_mm: number | null;
  flow: 'standard' | 'high_flow';
  ref: string;
  is_default_guess: boolean;
}

export interface ProfileSuggestions {
  provider_plugin_id: string | null;
  available: boolean;          // false: the provider can't suggest right now (manual entry still works); `error` says why
  candidates: ProfileCandidate[];
  variants: ProfileVariantOption[];
  error: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch { /* keep statusText */ }
    throw new Error(detail || `${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const variantOf = (m: Pick<ModelSlicerProfile, 'nozzle_diameter_mm' | 'flow'>): NozzleVariant =>
  ({ diameter_mm: m.nozzle_diameter_mm, flow: m.flow });

export async function fetchModelSlicerProfiles(modelUuid: string): Promise<ModelSlicerProfile[]> {
  const rows = await request<ModelSlicerProfile[]>(`/api/v1/printer-models/${encodeURIComponent(modelUuid)}/slicer-profiles`);
  return Array.isArray(rows) ? rows : [];     // a malformed body must never crash the pickers that render this
}

export function createModelSlicerProfile(modelUuid: string, body: {
  provider_plugin_id: string; nozzle_diameter_mm: number; flow?: 'standard' | 'high_flow'; machine_profile: string; is_default?: boolean;
}): Promise<ModelSlicerProfile> {
  return request(`/api/v1/printer-models/${encodeURIComponent(modelUuid)}/slicer-profiles`, json('POST', body));
}

export function updateModelSlicerProfile(modelUuid: string, id: number, body: { machine_profile?: string; is_default?: boolean }): Promise<ModelSlicerProfile> {
  return request(`/api/v1/printer-models/${encodeURIComponent(modelUuid)}/slicer-profiles/${id}`, json('PATCH', body));
}

export function deleteModelSlicerProfile(modelUuid: string, id: number): Promise<void> {
  return request(`/api/v1/printer-models/${encodeURIComponent(modelUuid)}/slicer-profiles/${id}`, { method: 'DELETE' });
}

export async function fetchProfileSuggestions(modelUuid: string, providerPluginId?: string): Promise<ProfileSuggestions> {
  const q = providerPluginId ? `?provider_plugin_id=${encodeURIComponent(providerPluginId)}` : '';
  const s = await request<Partial<ProfileSuggestions>>(`/api/v1/printer-models/${encodeURIComponent(modelUuid)}/slicer-profiles/suggestions${q}`);
  return { provider_plugin_id: s?.provider_plugin_id ?? providerPluginId ?? null, available: !!s?.available, error: s?.error ?? null,
           candidates: Array.isArray(s?.candidates) ? s.candidates : [], variants: Array.isArray(s?.variants) ? s.variants : [] };
}
