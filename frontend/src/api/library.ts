import { apiFetch } from './client';

export interface LibrarySource { plugin_id: string; name: string; enabled: boolean; active: boolean; error: string | null; last_sync_at: string | null }
export interface CatalogEntry { id: string; provider_plugin_id: string; source_file_id: string; name: string; source_url: string; missing: boolean; cached_file_id: number | null;
  metadata: { creator?: { name: string } | null; license_code?: string | null; size_bytes?: number | null } }
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Library source request failed');
  }
  return response.json();
}
export const fetchSources = () => request<{ sources: LibrarySource[] }>('/api/v1/library/sources');
export const fetchCatalog = (offset = 0) => request<{ items: CatalogEntry[]; total: number }>(`/api/v1/library/entries?limit=100&offset=${offset}`);
export const syncSource = (id: string) => request<{ entries: number; full_scan: boolean }>(`/api/v1/library/sources/${encodeURIComponent(id)}/sync`, { method: 'POST' });

export interface Materialization { file_id: number; fresh: boolean; warning: string | null }
export const materializeEntry = (id: string) => request<Materialization>(`/api/v1/library/entries/${encodeURIComponent(id)}/materialize`, { method: 'POST' });

export interface FileSourceOrigin {
  file_id: number; entry_id: string; provider_plugin_id: string; source_url: string;
  revision: string | null; downloaded_at: string; metadata: CatalogEntry['metadata'];
  is_latest: boolean; source_missing: boolean;
}
export const fetchFileOrigin = (id: number) => request<{ origin: FileSourceOrigin | null }>(`/api/v1/library/files/${id}/origin`);
