import type { FilamentAsk } from '../lib/filamentAsk';
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
  // The filament ask: manufacturer / type / subtype / color. null = undefined (matches anything); subtype "any" is a real value.
  filament_manufacturer: string | null;
  filament_type: string | null;
  filament_subtype: string | null;
  filament_color: string | null;
  sort_order: number;
  group_id: number | null;        // its print group (BIZ-360)
  quantity_held: number;          // failed parts held for a person after too many printers (BIZ-364)
  kind: 'stl' | '3mf_object' | '3mf_plate' | 'gcode';   // BIZ-356
  source_ref: string | null;      // object id, or plate number
  pack_rule: 'split' | 'together' | 'solo' | 'locked';
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
  price_visible: boolean;           // staff decide when the customer portal shows the quote
  quote_accepted_at: string | null; // when the customer accepted the visible quote
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
    /** Slicing cache: an identical earlier pack was reused instead of re-packing. */
    pack_reused: boolean;
    /** Slicing cache: how many of its plates already have cached gcode (used if a printer's slice matches). */
    cached_plates: number;
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
  price_visible?: boolean;
  customer_id?: number | null;
}

export interface ProjectItemCreate extends Partial<FilamentAsk> {
  file_id: number;
  quantity: number;
  sort_order?: number;
  group_id?: number;              // update only: move to another print group of the project (BIZ-360)
  /** BIZ-356: what of the file the item is (create only; omitted = inferred from the file) and its packing rule. */
  kind?: 'stl' | '3mf_object' | '3mf_plate' | 'gcode';
  source_ref?: string | null;
  pack_rule?: 'split' | 'together' | 'solo' | 'locked';
}

/** One eligibility entry of a print group: a PrinterTargetPicker key and its process profile ("" = Printer default). */
export interface ProjectGroupTarget {
  target_key: string;
  process_profile: string;
}

/** A print group (BIZ-360): its items print on its targets, packed for its envelope (null = auto), in group order. */
export interface ProjectGroup {
  id: number;
  project_id: number;
  name: string;
  sort_order: number;
  is_default: boolean;
  strategy: string;
  envelope_x: number | null;
  envelope_y: number | null;
  mix_colours: boolean;
  targets: ProjectGroupTarget[];
  max_parts: number | null;                 // pool: parts per claimed plate cap (null = as many as fit)
  pool: { done: number; printing: number; left: number } | null;   // pool groups only (BIZ-362)
  pool_warning: string | null;
}

export interface ProjectGroupPatch {
  name?: string;
  envelope_x?: number | null;
  envelope_y?: number | null;
  mix_colours?: boolean;
  targets?: ProjectGroupTarget[];
  strategy?: 'fixed' | 'pool';
  max_parts?: number | null;
}

/** One reported failure (BIZ-364): what failed where and when, and what happened to the failed parts. */
export interface ProjectFailure {
  id: number;
  project_item_id: number;
  file_name: string | null;
  job_id: number;
  printer_id: number | null;
  printer_name: string | null;
  quantity_failed: number;
  created_at: string | null;
  resolution: string | null;      // requeued | held | reprinted | dropped
}

export const listProjectFailures = (projectId: number) => request<ProjectFailure[]>(`/api/v1/projects/${projectId}/failures`);
export const resolveHeldParts = (projectId: number, itemId: number, action: 'reprint' | 'drop') =>
  request<{ requeued_job_ids: number[] }>(`/api/v1/projects/${projectId}/items/${itemId}/held`, json('POST', { action }));

export const listProjectGroups = (projectId: number) => request<ProjectGroup[]>(`/api/v1/projects/${projectId}/groups`);
export const createProjectGroup = (projectId: number, name: string) =>
  request<ProjectGroup>(`/api/v1/projects/${projectId}/groups`, json('POST', { name }));
export const patchProjectGroup = (projectId: number, groupId: number, body: ProjectGroupPatch) =>
  request<ProjectGroup>(`/api/v1/projects/${projectId}/groups/${groupId}`, json('PATCH', body));
export const orderProjectGroups = (projectId: number, groupIds: number[]) =>
  request<ProjectGroup[]>(`/api/v1/projects/${projectId}/groups/order`, json('PUT', { group_ids: groupIds }));
export async function deleteProjectGroup(projectId: number, groupId: number): Promise<void> {
  const resp = await apiFetch(`/api/v1/projects/${projectId}/groups/${groupId}`, { method: 'DELETE' });   // 204: no body
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text().catch(() => resp.statusText)}`);
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
export interface GenerateCacheOptions {
  /** Reuse an identical earlier pack and let each job print matching cached gcode (default on server-side too). */
  allowCached?: boolean;
  /** Keep each generated job's slice in the library as cached gcode. */
  saveSlice?: boolean;
}

/** Groups of printers eligible alongside the specific ones (BIZ-357): any printer of these models, or any printer at all. */
export interface GenerateGroups {
  modelUuids?: string[];
  anyPrinter?: boolean;
}

export const generateProject = (
  projectId: number, eligiblePrinterIds: number[] = [], processPreset: string | null = null,
  cache: GenerateCacheOptions = {}, groups: GenerateGroups = {},
) => request<GenerateOut>(`/api/v1/projects/${projectId}/generate`, json('POST', {
  eligible_printer_ids: eligiblePrinterIds,
  eligible_model_uuids: groups.modelUuids ?? [],
  eligible_any_printer: groups.anyPrinter ?? false,
  process_preset: processPreset,
  allow_cached: cache.allowCached ?? true,
  save_slice: cache.saveSlice ?? false,
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
