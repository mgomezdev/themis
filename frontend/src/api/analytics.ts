import { useEffect, useState } from 'react';
import { apiFetch } from './client';

export interface AnalyticsStats {
  completed: number;
  failed: number;
  cancelled: number;
  /** Percent 0–100; null when no job completed or failed in the range. */
  success_rate: number | null;
  print_seconds: number;
  filament_grams: number;
  /** null when no completed job has a recorded cost. */
  filament_cost: number | null;
}

export interface AnalyticsRange { start: string; end: string; days: number }

export interface AnalyticsPrinter extends AnalyticsStats {
  printer_id: number;
  name: string;
  utilization_pct: number;
}

export interface AnalyticsMaterial { material: string; grams: number; jobs: number }

export interface FleetAnalytics {
  range: AnalyticsRange;
  totals: AnalyticsStats;
  printers: AnalyticsPrinter[];
  materials: AnalyticsMaterial[];
}

export async function getFleetAnalytics(start: string, end: string): Promise<FleetAnalytics> {
  const r = await apiFetch(`/api/v1/fleet/analytics?start=${start}&end=${end}`);
  if (!r.ok) {
    const detail = await r.json().then((b: { detail?: unknown }) => b.detail).catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

/** Pass null (e.g. while a custom range is invalid) to hold off fetching. */
export function useFleetAnalytics(range: { start: string; end: string } | null) {
  const [data, setData] = useState<FleetAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const start = range?.start ?? null;
  const end = range?.end ?? null;

  useEffect(() => {
    if (start === null || end === null) return;
    let alive = true;
    setLoading(true);
    getFleetAnalytics(start, end)
      .then(d => { if (alive) { setData(d); setError(null); } })
      .catch(e => { if (alive) { setData(null); setError(e instanceof Error ? e.message : String(e)); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [start, end]);

  return { data, error, loading };
}
