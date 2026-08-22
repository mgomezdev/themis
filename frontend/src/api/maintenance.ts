// frontend/src/api/maintenance.ts
import { useCallback, useEffect, useState } from 'react';
import { fetchPrinters, fetchMachineCatalog, type ApiPrinter, type MachinePreset } from './printers';
import { apiFetch } from './client';

export type TriggerType = 'calendar' | 'job_time' | 'job_count';
export type CalendarUnit = 'hours' | 'days' | 'weeks' | 'months';

export interface MaintenanceTrigger {
  id?: number;
  trigger_type: TriggerType;
  amount: number;
  unit: CalendarUnit | null;
}

export interface MaintenanceItem {
  id: number;
  name: string;
  scope: 'general' | 'model';
  machine_vendor: string | null;
  machine_model: string | null;
  enabled: boolean;
  notes: string | null;
  triggers: MaintenanceTrigger[];
}

export interface MaintenanceTemplate {
  name: string;
  description: string;
  triggers: MaintenanceTrigger[];
}

export interface MaintenanceStatusRow {
  printer_id: number;
  printer_name: string;
  item_id: number;
  item_name: string;
  due: boolean;
  last_done_at: string;
}

export interface FleetVendorModel {
  vendor: string;
  printer_model: string;
}

export function resolveVendorModelForProfile(
  profile: string | null,
  catalog: Pick<MachinePreset, 'name' | 'vendor' | 'printer_model'>[],
): FleetVendorModel | null {
  if (!profile) return null;
  const match = catalog.find(c => c.name === profile);
  return match ? { vendor: match.vendor, printer_model: match.printer_model } : null;
}

export function resolveFleetVendorModels(
  printers: Pick<ApiPrinter, 'current_orca_printer_profile'>[],
  catalog: Pick<MachinePreset, 'name' | 'vendor' | 'printer_model'>[],
): FleetVendorModel[] {
  const seen = new Set<string>();
  const result: FleetVendorModel[] = [];
  for (const p of printers) {
    const resolved = resolveVendorModelForProfile(p.current_orca_printer_profile, catalog);
    if (!resolved) continue;
    const key = `${resolved.vendor} ${resolved.printer_model}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(resolved);
  }
  return result.sort((a, b) =>
    a.vendor.localeCompare(b.vendor) || a.printer_model.localeCompare(b.printer_model));
}

export function useFleetVendorModels(): FleetVendorModel[] {
  const [models, setModels] = useState<FleetVendorModel[]>([]);
  useEffect(() => {
    let alive = true;
    Promise.all([fetchPrinters(), fetchMachineCatalog()])
      .then(([printers, catalog]) => { if (alive) setModels(resolveFleetVendorModels(printers, catalog)); })
      .catch(console.error);
    return () => { alive = false; };
  }, []);
  return models;
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

export const getMaintenanceItems = () => request<MaintenanceItem[]>('/api/v1/maintenance/items');

export const createMaintenanceItem = (b: {
  name: string; scope: 'general' | 'model'; machine_vendor?: string | null;
  machine_model?: string | null; notes?: string | null; triggers: MaintenanceTrigger[];
}) => request<MaintenanceItem>('/api/v1/maintenance/items', jsonInit('POST', b));

export const updateMaintenanceItem = (id: number, b: Partial<Pick<MaintenanceItem,
  'name' | 'scope' | 'machine_vendor' | 'machine_model' | 'enabled' | 'notes'>>) =>
  request<MaintenanceItem>(`/api/v1/maintenance/items/${id}`, jsonInit('PATCH', b));

export const setMaintenanceTriggers = (id: number, triggers: MaintenanceTrigger[]) =>
  request<MaintenanceItem>(`/api/v1/maintenance/items/${id}/triggers`, jsonInit('PUT', { triggers }));

export const deleteMaintenanceItem = (id: number) =>
  request<{ deleted: number }>(`/api/v1/maintenance/items/${id}`, { method: 'DELETE' });

export const getMaintenanceTemplates = () => request<MaintenanceTemplate[]>('/api/v1/maintenance/templates');

export const getMaintenanceStatus = () => request<MaintenanceStatusRow[]>('/api/v1/maintenance/status');

export const completeMaintenanceItem = (printerId: number, itemId: number) =>
  request<{ printer_id: number; item_id: number; last_done_at: string }>(
    `/api/v1/maintenance/printers/${printerId}/items/${itemId}/complete`, { method: 'POST' });

export function useMaintenanceItems(): { items: MaintenanceItem[]; refetch: () => void } {
  const [items, setItems] = useState<MaintenanceItem[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getMaintenanceItems().then(d => { if (alive) setItems(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { items, refetch };
}

export function useMaintenanceStatus(): { rows: MaintenanceStatusRow[]; refetch: () => void } {
  const [rows, setRows] = useState<MaintenanceStatusRow[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getMaintenanceStatus().then(d => { if (alive) setRows(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { rows, refetch };
}
