import { useCallback, useEffect, useState } from 'react';
import type { LibraryFile, FolderNode } from '../data/types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export interface FileFilter { folder?: string; tags?: string[]; search?: string; sort?: string; }

export function getFiles(filter: FileFilter = {}): Promise<LibraryFile[]> {
  const q = new URLSearchParams();
  if (filter.folder) q.set('folder', filter.folder);
  if (filter.search) q.set('search', filter.search);
  if (filter.sort) q.set('sort', filter.sort);
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
export const updateFile = (id: number, b: { name?: string; folder?: string }) =>
  request<LibraryFile>(`/api/v1/files/${id}`, jsonInit('PATCH', b));
export const deleteFile = (id: number) =>
  request<{ deleted: number }>(`/api/v1/files/${id}`, { method: 'DELETE' });
export const addFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags`, jsonInit('POST', { tag_id: tagId }));
export const removeFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags/${tagId}`, { method: 'DELETE' });
export const rescanLibrary = () =>
  request<{ added: number; moved: number; removed: number; missing: number }>(
    '/api/v1/files/rescan', { method: 'POST' });
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
