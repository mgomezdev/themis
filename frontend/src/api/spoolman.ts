import { useState, useEffect, useCallback } from 'react';
import type { SyncResponse } from './laminus';
import { apiFetch } from './client';

export interface ApiFilament {
  id: number;
  name: string;
  vendor?: { id: number; name: string };
  material: string | null;
  color_hex?: string;
  settings_extruder_temp?: number;
  settings_bed_temp?: number;
  extra?: Record<string, unknown>;
}

export function filamentDisplayName(f: ApiFilament): string {
  return f.vendor?.name ? `${f.vendor.name} ${f.name}` : f.name;
}

export function parseOrcaProfiles(f: ApiFilament): Record<string, string[]> {
  try {
    const raw = f.extra?.orca_profiles;
    if (!raw) return {};
    // Spoolman text fields require the value to be a JSON-encoded string whose
    // content is itself valid JSON (double-encoded). Parse twice to reach the dict.
    let parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (typeof parsed === 'string') parsed = JSON.parse(parsed);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
    const result: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (Array.isArray(v) && v.every(x => typeof x === 'string')) result[k] = v;
    }
    return result;
  } catch (err) {
    console.warn('[Themis] orca_profiles parse error for filament', f.id, err);
    return {};
  }
}

export async function patchFilamentOrcaProfiles(
  filamentId: number,
  orcaProfiles: Record<string, string[]>,
): Promise<ApiFilament> {
  return request<ApiFilament>(`/api/v1/spoolman/filaments/${filamentId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orca_profiles: orcaProfiles }),
  });
}

export interface ApiSpool {
  id: number;
  filament: {
    id: number;
    vendor?: { name: string };
    name: string;
    material: string;
    color_hex?: string;
  };
  remaining_weight: number;
  used_weight: number;
}

export interface SpoolmanConfig {
  enabled: boolean;
  url: string | null;
  has_api_key: boolean;
  sync_interval_minutes: number;
}

// The API key never round-trips from the backend (see backend-review.md "Secrets").
// Omit `api_key` to leave it unchanged, "" to clear it, or a new value to set it.
export interface SpoolmanConfigUpdate {
  enabled?: boolean;
  url?: string | null;
  api_key?: string | null;
  sync_interval_minutes?: number;
}

export interface SpoolmanSyncStatus {
  enabled: boolean;
  interval_minutes: number;
  last_sync_at: string | null;
  last_attempt_at: string | null;
  last_error: string | null;
  last_error_code: string | null;
}

export type SpoolmanSyncTone = 'success' | 'fail' | 'stale';

/** success (green): synced recently, no error. fail (red): most recent attempt
 * errored. stale (orange): no error, but the last successful sync is more than
 * 2 sync intervals old (or there has never been one). */
export function spoolmanSyncTone(s: SpoolmanSyncStatus): SpoolmanSyncTone {
  if (s.last_error) return 'fail';
  if (!s.last_sync_at) return 'stale';
  const staleAfterMs = 2 * s.interval_minutes * 60_000;
  const age = Date.now() - new Date(s.last_sync_at).getTime();
  return age > staleAfterMs ? 'stale' : 'success';
}

export function spoolDisplayName(spool: ApiSpool): string {
  const vendor = spool.filament.vendor?.name;
  return vendor ? `${vendor} ${spool.filament.name}` : spool.filament.name;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

export async function getSpoolmanConfig(): Promise<SpoolmanConfig> {
  return request('/api/v1/settings/spoolman');
}

export async function saveSpoolmanConfig(cfg: SpoolmanConfigUpdate): Promise<SpoolmanConfig> {
  return request('/api/v1/settings/spoolman', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
}

export async function testSpoolmanConnection(
  url: string,
  api_key: string | null | undefined,
): Promise<SyncResponse> {
  const r = await apiFetch('/api/v1/settings/spoolman/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, api_key }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.message || `${r.status}`);
  }
  return r.json();
}

export interface SyncNowResponse {
  filament_count: number;
  spool_count: number;
}

export async function syncSpoolman(): Promise<SyncNowResponse> {
  return request('/api/v1/spoolman/sync-now', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
}

export async function getSpoolmanSyncStatus(): Promise<SpoolmanSyncStatus> {
  return request('/api/v1/spoolman/sync-status');
}

const SYNC_STATUS_POLL_MS = 30000;

export function useSpoolmanSyncStatus(): { status: SpoolmanSyncStatus | null; refetch: () => void } {
  const [status, setStatus] = useState<SpoolmanSyncStatus | null>(null);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    function poll() {
      getSpoolmanSyncStatus()
        .then(s => { if (alive) setStatus(s); })
        .catch(() => { /* leave last-known status in place */ });
    }
    poll();
    const id = setInterval(poll, SYNC_STATUS_POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [tick]);

  return { status, refetch };
}

export async function fetchFilaments(): Promise<ApiFilament[]> {
  return request('/api/v1/spoolman/filaments');
}

export async function fetchSpools(): Promise<ApiSpool[]> {
  return request('/api/v1/spoolman/spools');
}

export function useSpoolmanConfig(): { config: SpoolmanConfig | null; refetch: () => void } {
  const [config, setConfig] = useState<SpoolmanConfig | null>(null);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getSpoolmanConfig()
      .then(data => { if (alive) setConfig(data); })
      .catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  return { config, refetch };
}

export function useFilaments(enabled: boolean): ApiFilament[] {
  const [filaments, setFilaments] = useState<ApiFilament[]>([]);

  useEffect(() => {
    if (!enabled) { setFilaments([]); return; }
    let alive = true;
    fetchFilaments()
      .then(data => { if (alive) setFilaments(data); })
      .catch(() => { if (alive) setFilaments([]); });
    return () => { alive = false; };
  }, [enabled]);

  return filaments;
}

export function useSpools(enabled: boolean): ApiSpool[] {
  const [spools, setSpools] = useState<ApiSpool[]>([]);

  useEffect(() => {
    if (!enabled) { setSpools([]); return; }
    let alive = true;
    fetchSpools()
      .then(data => { if (alive) setSpools(data); })
      .catch(() => { if (alive) setSpools([]); });
    return () => { alive = false; };
  }, [enabled]);

  return spools;
}
