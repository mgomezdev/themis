import { apiFetch } from './client';

export interface LibrarySource { plugin_id: string; name: string; enabled: boolean; active: boolean; error: string | null; last_sync_at: string | null }
export interface CatalogEntry { id: string; provider_plugin_id: string; source_file_id: string; name: string; source_url: string; missing: boolean; cached_file_id: number | null;
  metadata: { creator?: { name: string } | null; license_code?: string | null; size_bytes?: number | null } }
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/api/v1/library${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Library source request failed');
  }
  return response.json();
}
export const fetchSources = () => request<{ sources: LibrarySource[] }>('/sources');
export const fetchCatalog = (offset = 0) => request<{ items: CatalogEntry[]; total: number }>(`/entries?limit=100&offset=${offset}`);
export const syncSource = (id: string) => request<{ entries: number; full_scan: boolean }>(`/sources/${encodeURIComponent(id)}/sync`, { method: 'POST' });
