import { useState, useEffect, useCallback } from 'react';

export interface ApiFilament {
  id: number;
  name: string;
  vendor?: { id: number; name: string };
  material: string;
  color_hex?: string;
  settings_extruder_temp?: number;
  settings_bed_temp?: number;
}

export function filamentDisplayName(f: ApiFilament): string {
  return f.vendor?.name ? `${f.vendor.name} ${f.name}` : f.name;
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
  api_key: string | null;
}

export function spoolDisplayName(spool: ApiSpool): string {
  const vendor = spool.filament.vendor?.name;
  return vendor ? `${vendor} ${spool.filament.name}` : spool.filament.name;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

export async function getSpoolmanConfig(): Promise<SpoolmanConfig> {
  return request('/api/v1/settings/spoolman');
}

export async function saveSpoolmanConfig(cfg: Partial<SpoolmanConfig>): Promise<SpoolmanConfig> {
  return request('/api/v1/settings/spoolman', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
}

export async function testSpoolmanConnection(
  url: string,
  api_key?: string | null,
): Promise<{ ok: boolean; version?: string; message?: string }> {
  return request('/api/v1/settings/spoolman/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, api_key }),
  });
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
