import { useCallback, useEffect, useState } from 'react';
import type { LibraryFile, FolderNode } from '../data/types';
import { apiFetch } from './client';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export type FileKindFilter = 'all' | 'models' | 'sliced';
export interface FileFilter { folder?: string; tags?: string[]; search?: string; sort?: string; kind?: FileKindFilter; }

export function getFiles(filter: FileFilter = {}): Promise<LibraryFile[]> {
  const q = new URLSearchParams();
  if (filter.folder) q.set('folder', filter.folder);
  if (filter.search) q.set('search', filter.search);
  if (filter.sort) q.set('sort', filter.sort);
  if (filter.kind && filter.kind !== 'all') q.set('kind', filter.kind);
  for (const t of filter.tags ?? []) q.append('tags', t);
  const qs = q.toString();
  return request<LibraryFile[]>(`/api/v1/files${qs ? `?${qs}` : ''}`);
}

export const getFolderTree = () => request<FolderNode>('/api/v1/files/tree');

/** Real on-disk folder hierarchy incl. empty folders (for the move picker). */
export const getFolderDirs = () => request<FolderNode>('/api/v1/files/dirs');

export async function uploadLibraryFile(file: File, folder?: string): Promise<LibraryFile> {
  const body = new FormData();
  body.append('file', file);
  if (folder) body.append('folder', folder);
  return request<LibraryFile>('/api/v1/files/upload', { method: 'POST', body });
}

export const createFolder = (path: string) =>
  request<{ path: string }>('/api/v1/files/folders', jsonInit('POST', { path }));
export const deleteFolder = (path: string) =>
  request<{ deleted: string }>(
    `/api/v1/files/folders?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
export const updateFile = (id: number, b: { name?: string; folder?: string }) =>
  request<LibraryFile>(`/api/v1/files/${id}`, jsonInit('PATCH', b));
export interface CachedVersionRef { id: number; file_id: number; name: string; folder: string }
export type DeleteFileResult =
  | { deleted: number; deleted_versions: number[] }
  | { needsChoice: true; versions: CachedVersionRef[] };

/** Delete a library file. A model with cached sliced versions answers 409 with them until `versions` says what to do
 * with them (`delete` together, or `keep` as standalone gcode) — returned as `needsChoice` rather than thrown. */
export async function deleteFile(id: number, versions?: 'delete' | 'keep'): Promise<DeleteFileResult> {
  const resp = await apiFetch(`/api/v1/files/${id}${versions ? `?versions=${versions}` : ''}`, { method: 'DELETE' });
  if (resp.status === 409) {
    const body = await resp.clone().json().catch(() => null) as { detail?: { versions?: CachedVersionRef[] } } | null;
    if (body?.detail && typeof body.detail === 'object' && Array.isArray(body.detail.versions)) {
      return { needsChoice: true, versions: body.detail.versions };
    }
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}
export const addFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags`, jsonInit('POST', { tag_id: tagId }));
export const removeFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags/${tagId}`, { method: 'DELETE' });
export const rescanLibrary = () =>
  request<{ added: number; moved: number; removed: number; missing: number }>(
    '/api/v1/files/rescan', { method: 'POST' });
/** A cached sliced version of a model (BIZ-193): what it was sliced with, and whether it's still a good match. */
export interface SlicedVersion {
  id: number;
  file_id: number;
  name: string;
  kind: 'gcode' | 'gcode_3mf';
  plate_number: number;
  machine_preset: string;
  process_preset: string;
  filament_presets: string[];
  filament_type: string;
  filament_color: string;
  bed_type: string | null;
  overrides: Record<string, string>;
  estimated_seconds: number | null;
  filament_grams: number | null;
  created_at: string;
  /** The model changed since this was sliced. */
  source_changed: boolean;
  /** Presets or OrcaSlicer changed since (null = couldn't tell — sidecar unreachable). */
  stale: boolean | null;
  stale_reasons: ('presets_changed' | 'slicer_version_changed')[];
  /** An enabled printer of that make/model can print it now. */
  printable_now: boolean;
}

export const getSlicedVersions = (fileId: number, plate?: number) =>
  request<SlicedVersion[]>(`/api/v1/files/${fileId}/sliced-versions${plate != null ? `?plate=${plate}` : ''}`);

export const fileThumbnailUrl = (f: LibraryFile) => f.thumbnail_url ?? undefined;

export function useFiles(filter: FileFilter): { files: LibraryFile[]; refetch: () => void } {
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  const key = JSON.stringify(filter);
  useEffect(() => {
    let alive = true;
    getFiles(JSON.parse(key)).then(d => { if (alive) setFiles(d); }).catch(console.error);
    return () => { alive = false; };
  }, [key, tick]);
  return { files, refetch };
}
