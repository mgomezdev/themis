import { useEffect, useState } from 'react';

export interface OrcaMachine {
  uuid: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  nozzle: string | null;
  extruder_count: number;
  bed_size_x: number | null;
  bed_size_y: number | null;
}

export interface OrcaProcess {
  uuid: string;
  name: string;
  compatible_printers: string[];
  layer_height: number | null;
}

export interface OrcaFilament {
  uuid: string;
  name: string;
  display_name: string;
  filament_type: string;
  filament_colour: string;
  filament_vendor: string;
  compatible_printers: string[];
}

export interface OrcaCatalog {
  machine: OrcaMachine[];
  process: OrcaProcess[];
  filament: OrcaFilament[];
}

export const getOrcaCatalog = (): Promise<OrcaCatalog> =>
  fetch('/api/v1/laminus/catalog').then(r => {
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  });

export interface OrcaCatalogStatus {
  cached: boolean;
  cached_bytes: number;
  fetched_at: number | null;
  laminus_configured: boolean;
  laminus: {
    catalog_loaded: boolean;
    catalog_building: boolean;
    profile_count: number | null;
  } | null;
  catalog_counts: { machine: number; process: number; filament: number } | null;
  status: 'online' | 'building' | 'offline' | 'unconfigured';
}

export const getOrcaCatalogStatus = (): Promise<OrcaCatalogStatus> =>
  fetch('/api/v1/laminus/catalog/status').then(r => r.json());

export function useOrcaCatalog() {
  const [catalog, setCatalog] = useState<OrcaCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    getOrcaCatalog()
      .then(d => { if (alive) { setCatalog(d); setLoading(false); } })
      .catch(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);
  return { catalog, loading };
}
