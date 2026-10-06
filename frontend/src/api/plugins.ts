import { useCallback, useSyncExternalStore } from 'react';
import { apiFetch } from './client';

export type PluginRenderer = 'default' | 'schema' | 'component';
export interface PluginTab { id: string; label: string; renderer: PluginRenderer }
export interface PluginUi {
  mode: 'section' | 'page';
  nav_label: string;
  nav_placement: 'settings' | 'main';
  nav_icon: string | null;
  tabs: PluginTab[];
}

export interface PluginSummary {
  id: string;
  name: string;
  kind: string;
  version: string;
  description: string;
  docs_url: string | null;
  source: string;                 // bundled | upload | github
  /** False for an installed package that is not running (staged for the next restart, or failed to load). */
  loaded?: boolean;
  /** What the installer knows about a non-bundled plugin; null/absent for bundled ones. */
  install?: PluginInstall | null;
  capabilities: string[];
  ui: PluginUi;
  /** The plugin's own switch (it also has to be the selected provider of its kind to be in use). */
  enabled: boolean;
  /** Selected for its kind AND enabled: the one instance core talks to. */
  active: boolean;
  error: string | null;
}

export interface PluginDetail extends PluginSummary {
  settings: Record<string, unknown>;
  /** Secret fields: whether a value is stored (the value itself never leaves the server). */
  secrets: Record<string, boolean>;
  settings_schema: JsonSchema;
  secret_fields: string[];
  state: Record<string, unknown>;
}

export interface JsonSchemaProperty {
  type?: string | string[];
  anyOf?: { type?: string }[];
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
}
export interface JsonSchema { properties?: Record<string, JsonSchemaProperty>; required?: string[] }

export interface PluginInstall {
  status: 'pending_restart' | 'active' | 'error' | 'pending_removal';
  version: string;
  previous_version: string | null;
  publisher: string | null;
  source_url: string | null;
  ref: string | null;
  commit_sha: string | null;
  archive_sha256: string;
  installed_at: string;
  error: string | null;
  can_rollback: boolean;
  can_check_updates: boolean;
}

export interface PendingChange { plugin_id: string; name: string; version: string; change: 'install' | 'update' | 'uninstall' }

export interface PluginList { plugins: PluginSummary[]; slots: Record<string, string | null>; pending?: PendingChange[] }

export interface PluginUpdate {
  enabled?: boolean;
  settings?: Record<string, unknown>;
  /** Write-only: omit a key to keep the stored value, "" to clear it. */
  secrets?: Record<string, string>;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '');
    let body: unknown = raw;
    try { body = JSON.parse(raw); } catch { /* plain text */ }
    const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : null;
    throw new Error(typeof detail === 'string' ? detail : typeof body === 'string' && body ? `${resp.status} ${body}` : `${resp.status}`);
  }
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const fetchPlugins = (): Promise<PluginList> => request('/api/v1/plugins');
export const fetchPlugin = (id: string): Promise<PluginDetail> => request(`/api/v1/plugins/${encodeURIComponent(id)}`);

export async function updatePlugin(id: string, patch: PluginUpdate): Promise<PluginDetail> {
  const detail = await request<PluginDetail>(`/api/v1/plugins/${encodeURIComponent(id)}`, json('PUT', patch));
  invalidatePlugins();
  return detail;
}

export async function testPlugin(id: string, draft: Pick<PluginUpdate, 'settings' | 'secrets'>): Promise<{ ok: boolean; message?: string; version?: string }> {
  return request(`/api/v1/plugins/${encodeURIComponent(id)}/test`, json('POST', draft));
}

export async function setExtensionSlot(kind: string, pluginId: string | null): Promise<{ kind: string; plugin_id: string | null }> {
  const r = await request<{ kind: string; plugin_id: string | null }>(`/api/v1/extension-slots/${encodeURIComponent(kind)}`, json('PUT', { plugin_id: pluginId }));
  invalidatePlugins();
  return r;
}

// ---- installation (admin session only) ----

export interface InstallPreview {
  token: string; id: string; name: string; version: string; kind: string; publisher: string | null; description: string;
  source: 'upload' | 'github'; source_url: string | null; ref: string | null; commit_sha: string | null; archive_sha256: string;
  min_themis: string | null;
}
export interface GithubSource { repo_url: string; ref?: string; subdir?: string }

export async function previewUpload(file: File): Promise<InstallPreview> {
  const body = new FormData();
  body.append('file', file);
  return (await request<{ preview: InstallPreview }>('/api/v1/plugins/install?preview=true', { method: 'POST', body })).preview;
}
export async function previewGithub(src: GithubSource): Promise<InstallPreview> {
  return (await request<{ preview: InstallPreview }>('/api/v1/plugins/install-from-github', json('POST', { ...src, preview: true }))).preview;
}
export async function commitInstall(token: string): Promise<void> {
  await request(`/api/v1/plugins/install/${encodeURIComponent(token)}/commit`, { method: 'POST' });
  invalidatePlugins();
}
export async function discardInstall(token: string): Promise<void> {
  await apiFetch(`/api/v1/plugins/install/${encodeURIComponent(token)}`, { method: 'DELETE' });
}
export const checkPluginUpdates = (id: string): Promise<{ update_available: boolean; ref: string; current_commit: string | null; latest_commit: string }> =>
  request(`/api/v1/plugins/${encodeURIComponent(id)}/updates`);
export async function previewUpgrade(id: string): Promise<InstallPreview> {
  return (await request<{ preview: InstallPreview }>(`/api/v1/plugins/${encodeURIComponent(id)}/upgrade`, json('POST', { preview: true }))).preview;
}
export async function rollbackPlugin(id: string): Promise<void> {
  await request(`/api/v1/plugins/${encodeURIComponent(id)}/rollback`, { method: 'POST' });
  invalidatePlugins();
}
export async function uninstallPlugin(id: string, removeData: boolean): Promise<void> {
  await request(`/api/v1/plugins/${encodeURIComponent(id)}?remove_data=${removeData}`, { method: 'DELETE' });
  invalidatePlugins();
}
export const fetchRestartStatus = (): Promise<{ pending: { plugin_id: string; version: string; status: string }[]; printing: string[] }> =>
  request('/api/v1/system/restart');
/** Restart Themis (applies every pending change). Resolves `{printing}` instead of restarting when printers are busy and `force` is false. */
export async function restartThemis(force: boolean): Promise<{ restarting: boolean; printing?: string[] }> {
  const resp = await apiFetch('/api/v1/system/restart', json('POST', { force }));
  if (resp.status === 409) {
    const body = await resp.json().catch(() => ({}));
    return { restarting: false, printing: body?.detail?.printers ?? [] };
  }
  if (!resp.ok) throw new Error(`${resp.status}`);
  return { restarting: true };
}

// ---- a tiny shared store: every hook sees the same list, and a change (provider switch, enable toggle) refreshes all ----

interface Store { data: PluginList | null; loaded: boolean }
let store: Store = { data: null, loaded: false };
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit(next: Store) { store = next; listeners.forEach(l => l()); }

function load(): Promise<void> {
  if (!inflight) {
    inflight = fetchPlugins()
      .then(data => emit({ data, loaded: true }))
      .catch(() => emit({ data: store.data, loaded: true }))      // unreachable / no permission: behave as "no plugins"
      .finally(() => { inflight = null; });
  }
  return inflight;
}

/** Drop the cached plugin list and refetch (after a plugin or slot change). */
export function invalidatePlugins(): void { void load(); }

/** Forget everything (tests). */
export function resetPluginStore(): void { store = { data: null, loaded: false }; inflight = null; }

function subscribe(l: () => void) {
  listeners.add(l);
  if (!store.loaded && !inflight) void load();
  return () => { listeners.delete(l); };
}

export function usePlugins(): { plugins: PluginSummary[]; slots: Record<string, string | null>; pending: PendingChange[]; loaded: boolean; refresh: () => void } {
  const s = useSyncExternalStore(subscribe, () => store);
  const refresh = useCallback(() => invalidatePlugins(), []);
  return { plugins: s.data?.plugins ?? [], slots: s.data?.slots ?? {}, pending: s.data?.pending ?? [], loaded: s.loaded, refresh };
}

/** The plugin currently in use for `kind` (selected and enabled), or null. */
export function useActivePlugin(kind: string): PluginSummary | null {
  const { plugins } = usePlugins();
  return plugins.find(p => p.kind === kind && p.active) ?? null;
}

/** Whether the active plugin of `kind` offers `capability`. False with no active plugin, or while loading. */
export function useCapability(kind: string, capability: string): boolean {
  const active = useActivePlugin(kind);
  return !!active && active.capabilities.includes(capability);
}

// ---- schema-renderer tabs (`renderer: 'schema'`): a UI the plugin describes, rendered by Themis ----

export type SchemaField =
  | { key: string; label: string; type: 'text' | 'number' | 'textarea' | 'checkbox'; required?: boolean; help?: string }
  | { key: string; label: string; type: 'select'; options: { value: string; label: string }[]; required?: boolean; help?: string };

export type SchemaBlock =
  | { type: 'form'; title?: string; description?: string; load: string; save: string; fields: SchemaField[] }
  | { type: 'table'; title?: string; description?: string; data: string; columns: { key: string; label: string }[];
      create?: { path: string; label?: string; fields: SchemaField[] };
      row_actions?: { label: string; method: 'POST' | 'DELETE' | 'PATCH'; path: string; confirm?: string; body?: Record<string, unknown> }[];
      empty?: string };

export interface TabSchema { title?: string; blocks: SchemaBlock[] }

export const fetchTabSchema = (pluginId: string, tabId: string): Promise<TabSchema> =>
  request(`/api/v1/plugins/${encodeURIComponent(pluginId)}/ui/${encodeURIComponent(tabId)}`);

/** A path relative to the plugin's own API (under the plugin's id), as the schema names it. */
export function pluginPath(pluginId: string, path: string, vars?: Record<string, unknown>): string {
  const filled = path.replace(/\{(\w+)\}/g, (_m, k) => encodeURIComponent(String(vars?.[k] ?? '')));
  return `/api/v1/plugins/${encodeURIComponent(pluginId)}/${filled.replace(/^\/+/, '')}`;
}

export const pluginRequest = request;
