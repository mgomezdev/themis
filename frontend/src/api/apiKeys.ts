// frontend/src/api/apiKeys.ts
import { apiFetch } from './client';

export interface ApiKeyOut {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  enabled: boolean;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKeyOut {
  key: string;
}

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

export const getApiKeys = () => request<ApiKeyOut[]>('/api/v1/api-keys');

export const getApiKeyScopes = () => request<string[]>('/api/v1/api-keys/scopes');

export const createApiKey = (name: string, scopes: string[]) =>
  request<ApiKeyCreated>('/api/v1/api-keys', jsonInit('POST', { name, scopes }));

export const revokeApiKey = (id: number) =>
  request<ApiKeyOut>(`/api/v1/api-keys/${id}/revoke`, { method: 'POST' });

export const deleteApiKey = (id: number) =>
  request<{ ok: boolean }>(`/api/v1/api-keys/${id}`, { method: 'DELETE' });

/** Mirrors the backend scope registry (app/auth.py) — hand-written, no shared codegen. */
export interface ScopeGroup {
  resource: string;
  label: string;
  scopes: { scope: string; label: string }[];
}

export const SCOPES: ScopeGroup[] = [
  { resource: 'files', label: 'Files', scopes: [
    { scope: 'files:read', label: 'Read' },
    { scope: 'files:write', label: 'Write' },
  ] },
  { resource: 'jobs', label: 'Jobs', scopes: [
    { scope: 'jobs:read', label: 'Read' },
    { scope: 'jobs:write', label: 'Write' },
  ] },
  { resource: 'printers', label: 'Printers', scopes: [
    { scope: 'printers:read', label: 'Read' },
    { scope: 'printers:write', label: 'Write' },
    { scope: 'printers:control', label: 'Control' },
  ] },
  { resource: 'queue', label: 'Queue', scopes: [
    { scope: 'queue:read', label: 'Read' },
    { scope: 'queue:write', label: 'Write' },
  ] },
  { resource: 'fleet', label: 'Fleet', scopes: [
    { scope: 'fleet:read', label: 'Read' },
  ] },
  { resource: 'orders', label: 'Orders', scopes: [
    { scope: 'orders:read', label: 'Read' },
    { scope: 'orders:write', label: 'Write' },
  ] },
  { resource: 'projects', label: 'Projects', scopes: [
    { scope: 'projects:read', label: 'Read' },
    { scope: 'projects:write', label: 'Write' },
  ] },
  { resource: 'laminus', label: 'Laminus', scopes: [
    { scope: 'laminus:read', label: 'Read' },
    { scope: 'laminus:write', label: 'Write' },
  ] },
  { resource: 'settings', label: 'Settings', scopes: [
    { scope: 'settings:read', label: 'Read' },
    { scope: 'settings:write', label: 'Write' },
  ] },
  { resource: 'spoolman', label: 'Spoolman', scopes: [
    { scope: 'spoolman:read', label: 'Read' },
    { scope: 'spoolman:write', label: 'Write' },
  ] },
  { resource: 'tags', label: 'Tags', scopes: [
    { scope: 'tags:read', label: 'Read' },
    { scope: 'tags:write', label: 'Write' },
  ] },
  { resource: 'maintenance', label: 'Maintenance', scopes: [
    { scope: 'maintenance:read', label: 'Read' },
    { scope: 'maintenance:write', label: 'Write' },
  ] },
  { resource: 'apikeys', label: 'API Keys', scopes: [
    { scope: 'apikeys:read', label: 'Read' },
    { scope: 'apikeys:write', label: 'Write' },
  ] },
];

export const ALL_SCOPES: string[] = SCOPES.flatMap(g => g.scopes.map(s => s.scope));
