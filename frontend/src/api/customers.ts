import { apiFetch } from './client';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(typeof body?.detail === 'string' ? body.detail : `${resp.status}`);
  }
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

// ---- Staff: customer accounts ----

export interface Customer {
  id: number;
  name: string;
  email: string;
  enabled: boolean;
  created_at: string;
}

export const listCustomers = () => request<Customer[]>('/api/v1/customers');
export const createCustomer = (body: { name: string; email: string; password: string }) =>
  request<Customer>('/api/v1/customers', json('POST', body));
export const updateCustomer = (id: number, body: Partial<{ name: string; email: string; password: string; enabled: boolean }>) =>
  request<Customer>(`/api/v1/customers/${id}`, json('PATCH', body));

// ---- Staff: project stage ----

export type ProjectStage = 'draft' | 'planning' | 'queued';
export const NEXT_STAGE: Record<ProjectStage, ProjectStage | null> = { draft: 'planning', planning: 'queued', queued: null };

export const promoteProject = (projectId: number, stage: ProjectStage) =>
  request<unknown>(`/api/v1/projects/${projectId}/promote`, json('POST', { stage }));

// ---- Customer portal ----

export interface PortalJob {
  id: number;
  status: string;
  plate_number: number;
  created_at: string;
  completed_at: string | null;
  estimate_seconds: number | null;
}

export interface PortalProject {
  id: number;
  name: string;
  notes: string | null;
  stage: ProjectStage;
  due_date: string | null;
  created_at: string;
  updated_at: string;
  items: { id: number; filename: string; quantity: number }[];
  jobs: PortalJob[];
  jobs_total: number;
  jobs_complete: number;
}

export const listMyProjects = () => request<PortalProject[]>('/api/v1/customer/projects');
export const createDraft = (body: { name: string; notes?: string }) =>
  request<PortalProject>('/api/v1/customer/projects', json('POST', body));
export const updateDraft = (id: number, body: { name?: string; notes?: string }) =>
  request<PortalProject>(`/api/v1/customer/projects/${id}`, json('PATCH', body));
export function uploadToDraft(id: number, file: File) {
  const fd = new FormData();
  fd.append('file', file);
  return request<PortalProject>(`/api/v1/customer/projects/${id}/files`, { method: 'POST', body: fd });
}
