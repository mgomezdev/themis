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

export interface SpoolmanPendingEntry {
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
    spoolman_filaments: SpoolmanPendingEntry[];
  };
  options: {
    machine: string[];
    process: string[];
    filament: string[];
  };
  spoolman_error: string | null;
}

export type SyncResponse = SyncOk | PendingRemaps;

export interface PrinterResolution {
  field: string;
  stale_value: string;
  new_value: string | null;
}

export interface JobResolution {
  field: string;
  stale_value: string;
  new_value: string | null;
}

export interface SpoolmanResolution {
  printer_preset: string;
  stale_name: string;
  new_name: string | null;
  affected_filament_ids: number[];
}

export interface Resolutions {
  printers: PrinterResolution[];
  jobs: JobResolution[];
  spoolman_filaments: SpoolmanResolution[];
}

export interface ConfirmResult {
  status: 'ok';
  applied: { printers: number; jobs: number; spoolman_filaments: number };
  spoolman_failures: string[];
}

export async function refreshCatalog(): Promise<SyncResponse> {
  const r = await fetch('/api/v1/laminus/catalog/refresh', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function rescanCatalog(): Promise<SyncResponse> {
  const r = await fetch('/api/v1/laminus/catalog/rescan', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function confirmRemap(syncId: string, resolutions: Resolutions): Promise<ConfirmResult> {
  const r = await fetch('/api/v1/laminus/catalog/confirm-remap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sync_id: syncId, resolutions }),
  });
  if (r.status === 409) throw Object.assign(new Error('sync_superseded'), { status: 409 });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
