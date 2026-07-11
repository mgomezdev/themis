const BASE = '/api/v1/printers';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
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
  spoolman_spool_id?: string | null;   // optional mapped Spoolman spool id
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

export function reconnectPrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/reconnect`, { method: 'POST' });
}

/** Mark the printer ready for new work (plate cleared) so it can claim the next job.
 *  Same endpoint a QR code / home-automation trigger would hit. */
export function markPlateCleared(id: string | number): Promise<{ ok: boolean }> {
  return request(`${BASE}/${id}/plate-cleared`, { method: 'POST' });
}
