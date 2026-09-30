/// <reference types="vite/client" />
// The shared response-key contract (repo-root contracts/response-keys.json) must match the TypeScript interfaces
// the frontend uses for the same endpoints, key for key. The backend has the mirror test
// (backend/tests/api/test_response_contracts.py) asserting real responses carry these keys.
import { describe, expect, it } from 'vitest';
import contractRaw from '../../../contracts/response-keys.json?raw';
import type { LibraryFile } from '../data/types';
import type { FleetPrinter } from './fleet';
import type { ApiPrinter } from './printers';
import type { Project, ProjectItem, ProjectJob, ProjectLink, ProjectPart } from './projects';
import type { ApiJob, ApiJobDetails } from './queue';

const contract = JSON.parse(contractRaw) as Record<string, string[]>;
const sorted = (keys: string[]) => [...new Set(keys)].sort();
const keysOf = (o: object) => sorted(Object.keys(o));

// `satisfies Record<keyof T, 1>` makes tsc reject a missing OR an extra key, so each literal tracks its interface.
const FLEET = {
  id: 1, name: 1, printer_type: 1, enabled: 1, queue_on: 1, connected: 1, awaiting_plate_clear: 1, no_snapshots_while_idle: 1,
  loaded_filaments: 1, state: 1, progress: 1, remaining_time: 1, layer_num: 1, total_layers: 1, temperatures: 1,
  capabilities: 1, current_print: 1, fan_model: 1, fan_aux: 1, fan_box: 1,
} satisfies Record<keyof FleetPrinter, 1>;

const PRINTER = {
  id: 1, name: 1, printer_type: 1, connection_config: 1, awaiting_plate_clear: 1, orca_printer_profiles: 1,
  current_orca_printer_profile: 1, enabled: 1, queue_on: 1, connected: 1, loaded_filaments: 1, build_plate_type: 1,
  no_snapshots_while_idle: 1, bed_x_mm: 1, bed_y_mm: 1,
} satisfies Record<keyof ApiPrinter, 1>;

const JOB = {
  id: 1, uploaded_file_id: 1, plate_number: 1, order_id: 1, assigned_printer_id: 1, queue_position: 1, status: 1,
  overrides: 1, block_reason: 1, actual_filament_grams: 1, actual_seconds: 1, actual_filament_breakdown: 1,
  deduction_skipped: 1, estimate_status: 1, estimate_seconds: 1, estimate_filament_grams: 1,
  estimate_filament_breakdown: 1, estimate_preset_label: 1, created_at: 1, updated_at: 1, materials: 1,
  eligible_printers: 1, low_stock_warning: 1, filament_cost: 1,
} satisfies Record<keyof ApiJob, 1>;

const JOB_DETAILS = {
  ...JOB, file: 1, plate: 1, printer_configs: 1, assigned_printer: 1, filament_grams_live: 1, estimated_seconds_live: 1,
} satisfies Record<keyof ApiJobDetails, 1>;

const PROJECT = {
  id: 1, name: 1, customer: 1, order_type: 1, on_hold: 1, due_date: 1, notes: 1, result_file_id: 1, source_app: 1,
  source_user: 1, source_layout_id: 1, amount_paid: 1, price: 1, payment_status: 1, stage: 1, customer_id: 1,
  customer_name: 1, created_at: 1,
  updated_at: 1, items: 1, links: 1, parts: 1, jobs_total: 1, jobs_complete: 1, estimate_filament_grams_total: 1,
  estimate_seconds_total: 1, estimate_filament_grams_remaining: 1, estimate_seconds_remaining: 1,
  actual_filament_grams: 1, actual_seconds: 1, filament_cost_total: 1,
} satisfies Record<keyof Project, 1>;

const PROJECT_ITEM = {
  id: 1, project_id: 1, file_id: 1, file_name: 1, quantity: 1, quantity_completed: 1, quantity_failed: 1,
  filament_type: 1, filament_color: 1, filament_id: 1, sort_order: 1,
} satisfies Record<keyof ProjectItem, 1>;

const PROJECT_LINK = { id: 1, project_id: 1, url: 1, label: 1, sort_order: 1, created_at: 1 } satisfies Record<keyof ProjectLink, 1>;

const PROJECT_PART = {
  id: 1, project_id: 1, name: 1, quantity: 1, allocated: 1, sort_order: 1, created_at: 1,
} satisfies Record<keyof ProjectPart, 1>;

const PROJECT_JOB = {
  id: 1, plate_number: 1, status: 1, queue_position: 1, assigned_printer_id: 1, block_reason: 1, outcome: 1,
  created_at: 1, updated_at: 1, completed_at: 1, file_name: 1, total_parts: 1,
} satisfies Record<keyof ProjectJob, 1>;

const LIBRARY_FILE = {
  id: 1, original_filename: 1, relative_path: 1, folder: 1, size_bytes: 1, plate_count: 1, uploaded_at: 1, missing: 1,
  tags: 1, thumbnail_url: 1, plate_thumbnails: 1,
} satisfies Record<keyof LibraryFile, 1>;

describe('contracts/response-keys.json matches the frontend interfaces', () => {
  const cases: [string, string[], string[]][] = [
    ['fleet printer (offline keys + connected-only keys)', [...contract.fleet_printer, ...contract.fleet_printer_connected_only], keysOf(FLEET)],
    ['printer', contract.printer, keysOf(PRINTER)],
    ['queue rows + job details core cover every ApiJob key', [...contract.queue_job, ...contract.job_details_core], keysOf(JOB)],
    ['job details (core + extra + queue-only enrichments)', [...contract.queue_job, ...contract.job_details_core, ...contract.job_details_extra], keysOf(JOB_DETAILS)],
    ['project', contract.project, keysOf(PROJECT)],
    ['project item', contract.project_item, keysOf(PROJECT_ITEM)],
    ['project link', contract.project_link, keysOf(PROJECT_LINK)],
    ['project part', contract.project_part, keysOf(PROJECT_PART)],
    ['project job', contract.project_job, keysOf(PROJECT_JOB)],
    ['library file', contract.library_file, keysOf(LIBRARY_FILE)],
  ];

  it.each(cases)('%s', (_name, fromContract, fromInterface) => {
    expect(sorted(fromContract)).toEqual(fromInterface);
  });

  it('has no unexamined entries (every contract list is compared above)', () => {
    const used = new Set(['_doc', 'fleet_printer', 'fleet_printer_connected_only', 'printer', 'queue_job', 'job_details_core',
      'job_details_extra', 'project', 'project_item', 'project_link', 'project_part', 'project_job', 'library_file']);
    expect(Object.keys(contract).sort()).toEqual([...used].sort());
  });
});
