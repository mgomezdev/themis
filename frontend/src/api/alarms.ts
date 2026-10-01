import { useCallback, useEffect, useState } from 'react';
import { apiFetch, openLiveSocket } from './client';

export type Severity = 'info' | 'warning' | 'error' | 'fatal';
export const SEVERITIES: Severity[] = ['info', 'warning', 'error', 'fatal'];

export interface PrinterAlarm {
  id: number;
  printer_id: number;
  printer_name: string | null;
  code: string;
  severity: Severity;
  message: string;
  source: string | null;
  help_url: string | null;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  active: boolean;
}

export interface AlarmSummary {
  count: number;
  worst: Severity | null;
  printers: Array<{ printer_id: number; count: number; worst: Severity }>;
}

export type AlarmStatus = 'active' | 'unacknowledged' | 'all';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text().catch(() => resp.statusText)}`);
  return resp.json();
}

export async function listAlarms(status: AlarmStatus = 'active', printerId?: number): Promise<PrinterAlarm[]> {
  const q = new URLSearchParams({ status });
  if (printerId != null) q.set('printer_id', String(printerId));
  const body = await request<PrinterAlarm[]>(`/api/v1/alarms?${q}`);
  return Array.isArray(body) ? body : [];
}

export function acknowledgeAlarm(id: number): Promise<PrinterAlarm> {
  return request(`/api/v1/alarms/${id}/acknowledge`, { method: 'POST' });
}

export function acknowledgeAll(printerId?: number): Promise<{ acknowledged: number }> {
  return request(`/api/v1/alarms/acknowledge-all${printerId != null ? `?printer_id=${printerId}` : ''}`, { method: 'POST' });
}

export function getAlarmSettings(): Promise<{ min_severity: Severity; severities: Severity[] }> {
  return request('/api/v1/alarms/settings');
}

export function saveAlarmSettings(minSeverity: Severity): Promise<{ min_severity: Severity }> {
  return request('/api/v1/alarms/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ min_severity: minSeverity }),
  });
}

/** Re-runs `load` on mount, whenever the server says alarms changed (WebSocket `alarms_changed`) and after a reconnect. */
export function useAlarmFeed<T>(load: () => Promise<T>, initial: T): [T, () => void] {
  const [data, setData] = useState<T>(initial);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    load().then(d => { if (alive) setData(d); }).catch(console.error);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  useEffect(() => openLiveSocket((e) => {
    try {
      if ((JSON.parse(e.data) as { type?: string }).type === 'alarms_changed') refetch();
    } catch { /* ignore malformed frames */ }
  }, refetch), [refetch]);

  return [data, refetch];
}

const EMPTY_SUMMARY: AlarmSummary = { count: 0, worst: null, printers: [] };

export function useAlarmSummary(): AlarmSummary {
  const [summary] = useAlarmFeed<AlarmSummary>(async () => {
    const body = await request<AlarmSummary>('/api/v1/alarms/summary');
    return typeof body?.count === 'number' ? body : EMPTY_SUMMARY;
  }, EMPTY_SUMMARY);
  return summary;
}
