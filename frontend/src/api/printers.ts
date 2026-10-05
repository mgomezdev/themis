import { apiFetch } from './client';

const BASE = '/api/v1/printers';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export interface ConnectionField {
  name: string;
  label: string;
  field_type: 'text' | 'password' | 'number';
  required: boolean;
  default: string | number | null;
  placeholder: string;
  help_text: string;
}

export interface PrinterType {
  printer_type: string;
  display_name: string;
  connection_fields: ConnectionField[];
}

export interface LoadedFilament {
  slot: number;
  filament_id: string | null;          // Bambu AMS code (e.g. "GFL99") or null — NOT a Spoolman id
  name: string;
  type: string;
  color: string;
  filament_profile?: string | null;    // OrcaSlicer filament preset used to slice with this filament
  /** The inventory spool loaded here: `{provider, spool_ref}` (null = unbound). Write this one. */
  inventory?: { provider: string; spool_ref: string } | null;
  spoolman_spool_id?: string | null;   // legacy mirror of `inventory.spool_ref` (read-only here; the server keeps it in sync)
}

/** What a slot is bound to: the `inventory` pair, else the legacy mirror (an older server / row that predates it). */
export function slotBinding(slot: Pick<LoadedFilament, 'inventory' | 'spoolman_spool_id'>): { provider: string; ref: string } | null {
  if (slot.inventory === null) return null;                     // explicitly unbound (the legacy mirror may still be echoed)
  if (slot.inventory?.spool_ref) return { provider: slot.inventory.provider, ref: slot.inventory.spool_ref };
  if (slot.spoolman_spool_id) return { provider: 'spoolman', ref: String(slot.spoolman_spool_id) };
  return null;
}

export interface ApiPrinter {
  id: number;
  name: string;
  printer_type: string;
  connection_config: Record<string, unknown>;
  awaiting_plate_clear: boolean;
  orca_printer_profiles: string[];
  current_orca_printer_profile: string | null;
  enabled: boolean;
  queue_on: boolean;
  connected: boolean;
  loaded_filaments: LoadedFilament[];
  build_plate_type: string | null;
  no_snapshots_while_idle: boolean;
  bed_x_mm: number;
  bed_y_mm: number;
  machine_rate_per_hour: number | null;   // per-printer override of the shop machine rate; null = shop rate
  quiet_start: string | null;   // server-local HH:MM; no new jobs start in [start, end)
  quiet_end: string | null;
}

export interface CreatePrinterBody {
  name: string;
  printer_type: string;
  connection_config: Record<string, unknown>;
  orca_printer_profiles?: string[];
  current_orca_printer_profile?: string | null;
  loaded_filaments?: LoadedFilament[];
}

export interface UpdatePrinterBody {
  name?: string;
  connection_config?: Record<string, unknown>;
  orca_printer_profiles?: string[];
  current_orca_printer_profile?: string | null;
  enabled?: boolean;
  queue_on?: boolean;
  loaded_filaments?: LoadedFilament[];
  build_plate_type?: string | null;
  no_snapshots_while_idle?: boolean;
  bed_x_mm?: number;
  bed_y_mm?: number;
  machine_rate_per_hour?: number | null;
  quiet_start?: string | null;
  quiet_end?: string | null;
}

export interface MachinePreset {
  name: string;
  vendor: string;
  printer_model: string;
  nozzle: string;
  source: 'system' | 'user';
}

export function fetchMachineCatalog(): Promise<MachinePreset[]> {
  return request(`${BASE}/orca-machine-catalog`);
}

export function rescanProfiles(): Promise<{ machine_presets: number }> {
  return request(`${BASE}/rescan-profiles`, { method: 'POST' });
}

export function fetchPrinterTypes(): Promise<PrinterType[]> {
  return request(`${BASE}/types`);
}

export function fetchPrinters(): Promise<ApiPrinter[]> {
  return request(BASE);
}

export function fetchPrinter(id: number): Promise<ApiPrinter> {
  return request(`${BASE}/${id}`);
}

export function createPrinter(body: CreatePrinterBody): Promise<ApiPrinter> {
  return request(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updatePrinter(id: number, body: UpdatePrinterBody): Promise<ApiPrinter> {
  return request(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deletePrinter(id: number): Promise<void> {
  return request(`${BASE}/${id}`, { method: 'DELETE' });
}

export function testConnection(body: {
  printer_type: string;
  connection_config: Record<string, unknown>;
}): Promise<{ ok: boolean; error?: string }> {
  return request(`${BASE}/test-connection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function pausePrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/pause`, { method: 'POST' });
}

export function resumePrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/resume`, { method: 'POST' });
}

export function stopPrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/stop`, { method: 'POST' });
}

export function setLight(id: string, on: boolean): Promise<void> {
  return request(`${BASE}/${id}/light`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on }),
  });
}

export function jogZ(id: string, distanceMm: number): Promise<void> {
  return request(`${BASE}/${id}/jog-z`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ distance_mm: distanceMm }),
  });
}

export function setFanSpeed(
  id: string,
  fan: 'model' | 'auxiliary' | 'box',
  speedPct: number,
): Promise<void> {
  return request(`${BASE}/${id}/fan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fan, speed_pct: speedPct }),
  });
}

export function setBedTemp(id: string, celsius: number): Promise<void> {
  return request(`${BASE}/${id}/bed-temp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ celsius }),
  });
}

export type Axis = 'X' | 'Y' | 'Z';

export function jogAxis(id: string | number, axis: Axis, distanceMm: number): Promise<void> {
  return request(`${BASE}/${id}/jog`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ axis, distance_mm: distanceMm }),
  });
}

export function homePrinter(id: string | number, axes: 'all' | Axis = 'all'): Promise<void> {
  return request(`${BASE}/${id}/home`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ axes }),
  });
}

export function setNozzleTemp(id: string | number, celsius: number): Promise<void> {
  return request(`${BASE}/${id}/nozzle-temp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ celsius }),
  });
}

export function setChamberTemp(id: string | number, celsius: number): Promise<void> {
  return request(`${BASE}/${id}/chamber-temp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ celsius }),
  });
}

/** Send a gcode/3mf/bgcode file straight to the printer, bypassing the queue; `start` also begins printing it. */
export function uploadToPrinter(
  id: string | number, file: File, start: boolean,
): Promise<{ ok: boolean; filename: string; started: boolean }> {
  const form = new FormData();
  form.append('file', file);
  form.append('start', start ? 'true' : 'false');
  return request(`${BASE}/${id}/upload`, { method: 'POST', body: form });
}

export function reconnectPrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/reconnect`, { method: 'POST' });
}

/** Mark the printer ready for new work (plate cleared) so it can claim the next job.
 *  Same endpoint a QR code / home-automation trigger would hit. */
export function markPlateCleared(id: string | number): Promise<{ ok: boolean }> {
  return request(`${BASE}/${id}/plate-cleared`, { method: 'POST' });
}

export interface DiscoveredPrinter {
  printer_type: string;
  display_name: string;
  ip: string;
  model: string | null;
  name: string | null;
  serial: string | null;
  connection_config: Record<string, string | number>;
  note: string | null;
  already_added: boolean;
}

export interface DiscoveryResult {
  ranges: string[];
  scanned: number;
  truncated: boolean;
  found: DiscoveredPrinter[];
}

/** Sweep the given IP ranges (empty = the server's own /24) for printers answering a vendor's discovery signature. */
export function discoverPrinters(ranges: string[]): Promise<DiscoveryResult> {
  return request(`${BASE}/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ranges }),
  });
}
