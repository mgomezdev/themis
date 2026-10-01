import { apiFetch } from './client';

/** Shop-wide hourly rates behind every project's expenses; applied live, so a change re-prices past jobs. */
export interface CostConfig {
  machine_rate_per_hour: number;
  labour_rate_per_hour: number;
}

export interface ProjectLabor {
  id: number;
  project_id: number;
  minutes: number;
  logged_on: string;          // YYYY-MM-DD
  note: string | null;
  created_at: string;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = body?.detail;
    throw new Error(typeof detail === 'string' ? detail
      : Array.isArray(detail) && detail[0]?.msg ? String(detail[0].msg) : `${resp.status}`);
  }
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getCostConfig = () => request<CostConfig>('/api/v1/settings/costs');
export const saveCostConfig = (cfg: CostConfig) => request<CostConfig>('/api/v1/settings/costs', json('PUT', cfg));

export const listLabor = (projectId: number) => request<ProjectLabor[]>(`/api/v1/projects/${projectId}/labor`);
export const addLabor = (projectId: number, body: { minutes: number; logged_on?: string; note?: string | null }) =>
  request<ProjectLabor>(`/api/v1/projects/${projectId}/labor`, json('POST', body));
export const deleteLabor = (projectId: number, laborId: number) =>
  request<{ deleted: number }>(`/api/v1/projects/${projectId}/labor/${laborId}`, { method: 'DELETE' });

export function fmtMinutes(m: number): string {
  const h = Math.floor(m / 60);
  const r = m % 60;
  return h > 0 ? (r ? `${h}h ${r}m` : `${h}h`) : `${r}m`;
}
