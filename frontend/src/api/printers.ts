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
  quick_add: boolean;   // an input on the scan's quick-add row (BIZ-369)
}

/** One printer model a plugin declares. The add-printer flow picks manufacturer -> model; the plugin serves the printer. */
export interface PrinterType {
  plugin_id: string;
  manufacturer_id: string;
  manufacturer_name: string;
  model_id: string;
  display_name: string;               // the model's name
  bed_mm: [number, number];
  toolheads: number;
  connection_fields: ConnectionField[];
  plugin_enabled: boolean;            // a disabled plugin's models are listed but cannot be added
  model_uuid: string | null;          // the core registry's stable id for this model
  model_enabled: boolean;             // the user's subset: only enabled models are offered when adding a printer
  custom: boolean;                    // a user-defined model: the add flow asks for its bed size (stored with the printer)
}

/** A registry entry (`GET /printer-models`): a stable UUID plus whether the model is usable right now. */
export interface PrinterModelEntry {
  id: string;
  plugin_id: string;
  manufacturer_id: string;
  manufacturer_name: string;
  model_id: string;
  display_name: string;
  bed_mm: [number, number];
  toolheads: number;
  enabled: boolean;
  dormant: boolean;
  dormant_reason: 'plugin_removed' | 'plugin_disabled' | 'model_removed' | null;
  printer_count: number;              // printers that reference this model (a dormant model stays while referenced)
  slicer_profile_count?: number;      // slicer machine profiles mapped for this model (BIZ-294); absent on a PATCH response
}

export interface LoadedFilament {
  slot: number;
  filament_id: string | null;          // Bambu AMS code (e.g. "GFL99") or null — NOT a Spoolman id
  name: string;
  type: string;
  color: string;
  subtype?: string | null;             // "standard" (plain), "plus", "silk", "cf"... — the server always supplies one for a typed slot
  manufacturer?: string | null;        // the maker, null = unknown
  reported?: boolean;                  // the printer's hardware reports this slot (telemetry replaces its tuple on every report)
  empty?: boolean;                     // a tool-changer placeholder for an empty tool
  filament_profile?: string | null;    // OrcaSlicer filament preset used to slice with this filament
  /** The inventory spool loaded here: `{provider, spool_ref}` (null = unbound). Write this one. */
  inventory?: { provider: string; spool_ref: string } | null;
  spoolman_spool_id?: string | null;   // legacy mirror of `inventory.spool_ref` (read-only here; the server keeps it in sync)
}

/** The provider every pre-namespacing row (a bare numeric `filament_id`, a `spoolman_spool_id`) belongs to. */
export const LEGACY_PROVIDER = 'spoolman';

/** What a slot is bound to: the `inventory` pair, else the legacy mirror (an older server / row that predates it). */
export function slotBinding(slot: Pick<LoadedFilament, 'inventory' | 'spoolman_spool_id'>): { provider: string; ref: string } | null {
  if (slot.inventory === null) return null;                     // explicitly unbound (the legacy mirror may still be echoed)
  if (slot.inventory?.spool_ref) return { provider: slot.inventory.provider, ref: slot.inventory.spool_ref };
  if (slot.spoolman_spool_id) return { provider: LEGACY_PROVIDER, ref: String(slot.spoolman_spool_id) };
  return null;
}

/** A physical nozzle: diameter and flow. A model maps one slicer machine profile per variant (BIZ-294). */
export interface NozzleVariant {
  diameter_mm: number;
  flow: 'standard' | 'high_flow';
}

export const variantLabel = (v: NozzleVariant): string => `${v.diameter_mm}mm${v.flow === 'high_flow' ? ' high-flow' : ''}`;
export const sameVariant = (a: NozzleVariant | null | undefined, b: NozzleVariant | null | undefined): boolean =>
  !!a && !!b && a.diameter_mm === b.diameter_mm && a.flow === b.flow;

export interface ApiPrinter {
  id: number;
  name: string;
  printer_type: string;
  plugin_id: string | null;
  manufacturer_id: string | null;
  model_id: string | null;
  model_uuid: string | null;
  connection_config: Record<string, unknown>;
  awaiting_plate_clear: boolean;
  orca_printer_profiles: string[];
  machine_profile_override: string | null;   // advanced: a raw slicer machine profile that wins over the model's mapping
  nozzle_variant: NozzleVariant | null;      // the physical nozzle; null = the model's default variant
  machine_profile: string | null;            // what this printer slices with now (override, else model mapping + variant)
  machine_profile_reason: string | null;     // why `machine_profile` is null ("no machine profile for P1S 0.6mm")
  model_label: string | null;                // "<manufacturer> <model>" of the printer's registry model
  enabled: boolean;
  queue_on: boolean;
  configured: boolean;            // BIZ-369: a connection confirmed its setup; only configured printers take work
  config_error: string | null;    // last test failure / missing-field reason
  connected: boolean;
  loaded_filaments: LoadedFilament[];
  build_plate_type: string | null;
  no_snapshots_while_idle: boolean;
  bed_x_mm: number;
  bed_y_mm: number;
  bed_z_mm: number | null;               // BIZ-363: build height; null = the machine profile's (Review fit check)
  machine_rate_per_hour: number | null;   // per-printer override of the shop machine rate; null = shop rate
  preferred_slicer_plugin_id: string | null;   // the gcode.produce plugin this printer slices with; null = the capability default
  default_process_profile: string | null;   // what a "Printer default" job config slices with; null = none set
  single_colour_only: boolean;              // never takes multicolour work
  waiver_flag_at: number;                   // BIZ-365: flag at this many waived parts (0 = off)
  waiver_stop_at: number;                   // stop taking queue work at this many (0 = off)
  test_print_job_id: number | null;         // a pending test print: the only job it claims
  quiet_start: string | null;   // server-local HH:MM; no new jobs start in [start, end)
  quiet_end: string | null;
}

export interface CreatePrinterBody {
  name?: string;                        // default: "<model> <last IP octet>"
  plugin_id: string;
  manufacturer_id: string;
  model_id: string;
  connection_config: Record<string, unknown>;
  bed_x_mm?: number;                    // default: the declared model's bed (a custom model's is the user's input)
  bed_y_mm?: number;
  orca_printer_profiles?: string[];
  machine_profile_override?: string | null;
  nozzle_variant?: NozzleVariant | null;
  loaded_filaments?: LoadedFilament[];
}

export interface UpdatePrinterBody {
  name?: string;
  manufacturer_id?: string;
  model_id?: string;
  connection_config?: Record<string, unknown>;
  orca_printer_profiles?: string[];
  machine_profile_override?: string | null;
  nozzle_variant?: NozzleVariant | null;   // null = the model's default variant; omit to leave unchanged
  enabled?: boolean;
  queue_on?: boolean;
  loaded_filaments?: LoadedFilament[];
  build_plate_type?: string | null;
  no_snapshots_while_idle?: boolean;
  bed_x_mm?: number;
  bed_y_mm?: number;
  bed_z_mm?: number | null;                     // null clears (use the machine profile's height)
  machine_rate_per_hour?: number | null;
  preferred_slicer_plugin_id?: string | null;   // null clears (use the default); omit to leave unchanged
  default_process_profile?: string | null;     // null clears; must be compatible with the printer's machine profile
  single_colour_only?: boolean;
  waiver_flag_at?: number;
  waiver_stop_at?: number;
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

export function fetchPrinterModels(): Promise<PrinterModelEntry[]> {
  return request('/api/v1/printer-models');
}

export function setPrinterModelEnabled(id: string, enabled: boolean): Promise<PrinterModelEntry> {
  return request(`/api/v1/printer-models/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  });
}

export function fetchPrinters(): Promise<ApiPrinter[]> {
  return request(BASE);
}

/** Indexes of the slots whose bound spool differs from what the printer reports (skipped by the queue's filament matching). */
export interface SlotConflicts { conflicts: number[] }
export function fetchSlotConflicts(id: number): Promise<SlotConflicts> {
  return request(`${BASE}/${id}/slot-conflicts`);
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

export interface SavedPrinterTest {
  ok: boolean;
  error: string | null;
  configured: boolean;
  config_error: string | null;
}

/** Test a saved printer with its stored config; success makes it configured (BIZ-369). */
export function testSavedPrinter(id: number): Promise<SavedPrinterTest> {
  return request(`${BASE}/${id}/test`, { method: 'POST' });
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
export function markPlateCleared(id: string | number, results?: PlateResultsIn): Promise<PlateClearedOut> {
  return request(`${BASE}/${id}/plate-cleared`, results
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(results) }
    : { method: 'POST' });
}

/** What printed on the plate being cleared (BIZ-364): parts not listed all succeeded. */
export interface PlateResultsIn {
  job_id: number;
  results: { project_item_id: number; quantity_failed: number }[];
  clear_waiver?: boolean;          // BIZ-365: a test print that came out fine clears its printer's waiver
}

export interface PlateClearedOut {
  ok: boolean;
  requeued_job_ids?: number[];                                  // failed parts packed again (reported results only)
  held?: { project_item_id: number; quantity: number }[];       // failed on too many printers: a person decides
}

/** The project job whose results are still to report on this printer's plate; `job_id` null = nothing to ask. */
export interface PlateResults {
  job_id: number | null;
  project_id: number | null;
  parts: { project_item_id: number; file_name: string; quantity: number }[];
  test_print_waiver_id: number | null;    // BIZ-365: this job is the printer's test print for that waiver
}

/** A part this printer keeps failing and no longer gets (BIZ-365). */
export interface PartWaiver {
  id: number;
  part_key: string;
  file_name: string | null;
  failures: number;
  last_failure_at: string | null;
  projects: string[];
}

export const listWaivers = (id: number) => request<PartWaiver[]>(`${BASE}/${id}/waivers`);
export async function clearWaiver(id: number, waiverId: number): Promise<void> {
  const resp = await apiFetch(`${BASE}/${id}/waivers/${waiverId}`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text().catch(() => resp.statusText)}`);
}
export async function clearAllWaivers(id: number): Promise<void> {
  const resp = await apiFetch(`${BASE}/${id}/waivers`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text().catch(() => resp.statusText)}`);
}
export const testPrintWaiver = (id: number, waiverId: number) =>
  request<{ job_id: number }>(`${BASE}/${id}/waivers/${waiverId}/test-print`, { method: 'POST' });

export function getPlateResults(id: string | number): Promise<PlateResults> {
  return request(`${BASE}/${id}/plate-results`);
}

export interface DiscoveredPrinter {
  model_uuid: string | null;       // the registry model the announcement matched, or null (the user picks a model / custom)
  printer_type: string;
  plugin_id: string | null;
  display_name: string;
  ip: string;
  model: string | null;
  name: string | null;
  serial: string | null;
  connection_config: Record<string, string | number>;
  note: string | null;
  already_added: boolean;          // a saved printer has this (IP, model)
  added_printer: { printer_id: number; name: string; configured: boolean; config_error: string | null } | null;
  ip_in_use_by: { printer_id: number; name: string; model: string | null } | null;   // the IP belongs to a printer of another model
}

export interface DiscoveryResult {
  ranges: string[];
  scanned: number;
  truncated: boolean;
  found: DiscoveredPrinter[];
}

/** Sweep the given IP ranges (empty = the server's own /24) for printers answering a vendor's discovery signature. */
export function discoverPrinters(ranges: string[], pluginId?: string): Promise<DiscoveryResult> {
  return request(`${BASE}/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ranges, ...(pluginId ? { plugin_id: pluginId } : {}) }),
  });
}
