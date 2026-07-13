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
  quantity_completed: number;
  quantity_failed: number;
  filament_type: string;    // "any" | "PLA" | "PETG" | ...
  filament_color: string;   // "any" | "#RRGGBB"
  filament_id: number | null; // Spoolman filament ID, or null
  sort_order: number;
}

export interface ProjectLink {
  id: number;
  project_id: number;
  url: string;
  label: string | null;
  sort_order: number;
  created_at: string;
}

export interface Project {
  id: number;
  name: string;
  customer: string;
  order_type: string;       // "internal" | "customer"
  on_hold: boolean;
  due_date: string | null;
  notes: string | null;
  result_file_id: number | null;
  source_app: string | null;
  source_user: string | null;
  source_layout_id: number | null;
  created_at: string;
  updated_at: string;
  items: ProjectItem[];
  links: ProjectLink[];
  jobs_total: number;
  jobs_complete: number;
  estimate_filament_grams_total: number | null;
  estimate_seconds_total: number | null;
  estimate_filament_grams_remaining: number | null;
  estimate_seconds_remaining: number | null;
  actual_filament_grams: number | null;
  actual_seconds: number | null;
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
  eligible_printer_ids: number[];
  pack_bed_x: number;
  pack_bed_y: number;
}

export interface ProjectCreate {
  name: string;
  customer?: string;
  order_type?: string;
  on_hold?: boolean;
  due_date?: string | null;
  notes?: string | null;
  source_app?: string | null;
  source_user?: string | null;
  source_layout_id?: number | null;
}

export interface ProjectItemCreate {
  file_id: number;
  quantity: number;
  filament_type: string;
  filament_color: string;
  filament_id?: number | null;
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
export const generateProject = (projectId: number, eligiblePrinterIds: number[] = []) =>
  request<GenerateOut>(`/api/v1/projects/${projectId}/generate`, json('POST', { eligible_printer_ids: eligiblePrinterIds }));

export interface ProjectJob {
  id: number;
  plate_number: number;
  status: string;
  queue_position: number | null;
  assigned_printer_id: number | null;
  block_reason: string | null;
  outcome: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  file_name: string | null;
  total_parts: number;
}

export const getProjectJobs = (projectId: number) =>
  request<ProjectJob[]>(`/api/v1/projects/${projectId}/jobs`);

export interface ProjectLinkCreate {
  url: string;
  label?: string | null;
  sort_order?: number;
}

export const getProjectLinks = (projectId: number) =>
  request<ProjectLink[]>(`/api/v1/projects/${projectId}/links`);
export const addProjectLink = (projectId: number, body: ProjectLinkCreate) =>
  request<ProjectLink>(`/api/v1/projects/${projectId}/links`, json('POST', body));
export const updateProjectLink = (projectId: number, linkId: number, body: Partial<ProjectLinkCreate>) =>
  request<ProjectLink>(`/api/v1/projects/${projectId}/links/${linkId}`, json('PUT', body));
export const deleteProjectLink = (projectId: number, linkId: number) =>
  request<{ deleted: number }>(`/api/v1/projects/${projectId}/links/${linkId}`, { method: 'DELETE' });

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
