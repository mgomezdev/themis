import type { ProjectStage } from './customers';
import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from './client';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
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

/** Filament + machine time + labour + bought-in parts, at the *current* rates (see Settings → Costs). */
export interface ProjectCosts {
  filament: number;
  machine: number;
  labour: number;
  parts: number;
  machine_hours: number;
  labour_hours: number;
  total: number;
}

export interface ProjectPart {
  id: number;
  project_id: number;
  name: string;
  quantity: number;
  allocated: boolean;
  sort_order: number;
  created_at: string;
  unit_cost: number | null;   // cost of one unit; the parts expense is quantity × unit_cost
}

export type PaymentStatus = 'unpaid' | 'partial' | 'paid';

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
  amount_paid: number | null;
  price: number | null;     // quoted total; outstanding = price - amount_paid
  payment_status: PaymentStatus;
  costs: ProjectCosts;               // what it cost to make, at the current shop rates
  stage: ProjectStage;
  customer_id: number | null;
  customer_name: string | null;  // name of the linked customer account, if any
  created_at: string;
  updated_at: string;
  items: ProjectItem[];
  links: ProjectLink[];
  parts: ProjectPart[];
  jobs_total: number;
  jobs_complete: number;
  estimate_filament_grams_total: number | null;
  estimate_seconds_total: number | null;
  estimate_filament_grams_remaining: number | null;
  estimate_seconds_remaining: number | null;
  actual_filament_grams: number | null;
  actual_seconds: number | null;
  filament_cost_total: number | null;
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
  amount_paid?: number | null;
  price?: number | null;
  payment_status?: PaymentStatus;
  customer_id?: number | null;
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
export const generateProject = (
  projectId: number, eligiblePrinterIds: number[] = [], processPreset: string | null = null,
) => request<GenerateOut>(`/api/v1/projects/${projectId}/generate`, json('POST', {
  eligible_printer_ids: eligiblePrinterIds,
  process_preset: processPreset,
}));

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

export interface ProjectShare {
  enabled: boolean;
  token: string | null;
  created_at: string | null;
}

export const getProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`);
export const createOrRegenerateProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`, { method: 'PUT' });
export const revokeProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`, { method: 'DELETE' });

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

export interface ProjectPartCreate {
  name: string;
  quantity: number;
  allocated?: boolean;
  sort_order?: number;
  unit_cost?: number | null;
}

export const getProjectParts = (projectId: number) =>
  request<ProjectPart[]>(`/api/v1/projects/${projectId}/parts`);
export const addProjectPart = (projectId: number, body: ProjectPartCreate) =>
  request<ProjectPart>(`/api/v1/projects/${projectId}/parts`, json('POST', body));
export const updateProjectPart = (projectId: number, partId: number, body: Partial<ProjectPartCreate>) =>
  request<ProjectPart>(`/api/v1/projects/${projectId}/parts/${partId}`, json('PUT', body));
export const deleteProjectPart = (projectId: number, partId: number) =>
  request<{ deleted: number }>(`/api/v1/projects/${projectId}/parts/${partId}`, { method: 'DELETE' });

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
