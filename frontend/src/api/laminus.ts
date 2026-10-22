import { apiFetch } from './client';

export interface SyncOk {
  status: 'ok';
  bytes: number;
}

export interface PrinterPendingEntry {
  field: string;
  stale_value: string;
  options_kind: 'machine' | 'filament';
  required: true;
  affected_printer_ids: number[];
  affected_printer_names: string[];
  affected_slots: (number | null)[];
}

export interface JobPendingEntry {
  field: string;
  stale_value: string;
  options_kind: 'process' | 'filament';
  required: false;
  affected_config_ids: number[];
  affected_file_names: string[];
}

export interface InventoryPendingEntry {
  printer_preset: string;
  stale_name: string;
  required: false;
  affected_filament_ids: number[];
  affected_filament_names: string[];
}

export interface PendingRemaps {
  status: 'pending_remaps';
  sync_id: string;
  pending: {
    printers: PrinterPendingEntry[];
    jobs: JobPendingEntry[];
    inventory_filaments: InventoryPendingEntry[];
  };
  options: {
    machine: string[];
    process: string[];
    filament: string[];
  };
  inventory_error: string | null;
}

export type SyncResponse = SyncOk | PendingRemaps;

export interface ProfileResolution {
  field: string;
  stale_value: string;
  new_value: string | null;
}

export interface InventoryResolution {
  printer_preset: string;
  stale_name: string;
  new_name: string | null;
  affected_filament_ids: number[];
}

export interface Resolutions {
  printers: ProfileResolution[];
  jobs: ProfileResolution[];
  inventory_filaments: InventoryResolution[];
}

export interface ConfirmResult {
  status: 'ok';
  applied: { printers: number; jobs: number; inventory_filaments: number };
  inventory_failures: string[];
}

/** How many references a confirmed remap rewrote, whatever they were (printers, jobs, inventory materials). */
export const appliedRemapTotal = (r: ConfirmResult): number => r.applied.printers + r.applied.jobs + r.applied.inventory_filaments;

export async function refreshCatalog(): Promise<SyncResponse> {
  const r = await apiFetch('/api/v1/laminus/catalog/refresh', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function rescanCatalog(): Promise<SyncResponse> {
  const r = await apiFetch('/api/v1/laminus/catalog/rescan', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function confirmRemap(syncId: string, resolutions: Resolutions): Promise<ConfirmResult> {
  const r = await apiFetch('/api/v1/laminus/catalog/confirm-remap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sync_id: syncId, resolutions }),
  });
  if (r.status === 409) throw Object.assign(new Error('sync_superseded'), { status: 409 });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
