import { useCallback, useEffect, useState } from 'react';
import type { LibraryFile } from '../data/types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export interface ProjectItem {
  id: number;
  project_id: number;
  file_id: number;
  file_name: string;
  quantity: number;
  filament_profile_uuid: string;
  filament_display_name: string | null;
  color_hex: string;
  sort_order: number;
}

export interface Project {
  id: number;
  name: string;
  machine_uuid: string;
  process_uuid: string;
  notes: string | null;
  result_file_id: number | null;
  created_at: string;
  updated_at: string;
  items: ProjectItem[];
}

export interface GenerateOut {
  project_id: number;
  jobs: {
    id: number;
    uploaded_file_id: number;
    plate_number: number;
    queue_position: number;
    status: string;
  }[];
  files: {
    id: number;
    original_filename: string;
    folder: string;
    plate_count: number;
  }[];
}

export interface ProjectCreate {
  name: string;
  machine_uuid: string;
  process_uuid: string;
  notes?: string | null;
}

export interface ProjectItemCreate {
  file_id: number;
  quantity: number;
  filament_profile_uuid: string;
  color_hex: string;
  sort_order?: number;
}

export const getProjects = () => request<Project[]>('/api/v1/projects');
export const getProject = (id: number) => request<Project>(`/api/v1/projects/${id}`);
export const createProject = (body: ProjectCreate) =>
  request<Project>('/api/v1/projects', json('POST', body));
export const patchProject = (id: number, body: Partial<ProjectCreate>) =>
  request<Project>(`/api/v1/projects/${id}`, json('PATCH', body));
export const deleteProject = (id: number) =>
  request<{ deleted: number }>(`/api/v1/projects/${id}`, { method: 'DELETE' });
export const addProjectItem = (projectId: number, body: ProjectItemCreate) =>
  request<ProjectItem>(`/api/v1/projects/${projectId}/items`, json('POST', body));
export const updateProjectItem = (
  projectId: number, itemId: number, body: Partial<ProjectItemCreate>,
) => request<ProjectItem>(`/api/v1/projects/${projectId}/items/${itemId}`, json('PUT', body));
export const deleteProjectItem = (projectId: number, itemId: number) =>
  request<{ deleted: number }>(
    `/api/v1/projects/${projectId}/items/${itemId}`, { method: 'DELETE' },
  );
export const reorderProjectItems = (
  projectId: number, items: { id: number; sort_order: number }[],
) => request<ProjectItem[]>(`/api/v1/projects/${projectId}/items/reorder`, json('PUT', items));
export const generateProject = (projectId: number) =>
  request<GenerateOut>(`/api/v1/projects/${projectId}/generate`, { method: 'POST' });

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getProjects().then(d => { if (alive) setProjects(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { projects, refetch };
}

export function useProjectFiles(id: number) {
  const [file, setFile] = useState<LibraryFile | null>(null);
  useEffect(() => {
    if (!id) return;
    let alive = true;
    fetch(`/api/v1/files/${id}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (alive) setFile(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [id]);
  return file;
}
