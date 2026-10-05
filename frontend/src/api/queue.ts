import { useState, useEffect, useCallback } from 'react';
import { apiFetch, withKeyParam, openLiveSocket } from './client';

export interface ApiPlate {
  plate_number: number;
  estimated_time: number;
  filament_g: number;
  thumbnail_path: string | null;
}

export interface ApiUploadedFile {
  id: number;
  original_filename: string;
  folder: string;
  plate_count: number;
}

export interface PrinterProfiles {
  print_profiles: string[];
  filament_profiles: string[];
}

export interface ModelFilament {
  index: number;
  color: string;
  type: string;
}

export interface PrinterConfigInput {
  printer_id: number;
  print_profile: string;
  filament_profile?: string | null;
  filament_id?: number | null;
  material_provider?: string | null;      // the provider-namespaced material pick (preferred over filament_id)
  material_ref?: string | null;
  filament_type?: string | null;
  filament_color?: string | null;
  tool_index?: number | null;
  filament_map?: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
}

/** Eligible on any printer whose active machine preset equals `machine_profile` (a make/model). */
export interface ModelTargetInput {
  machine_profile: string;
  print_profile: string;
  filament_profile?: string | null;
  filament_id?: number | null;
  material_provider?: string | null;
  material_ref?: string | null;
  filament_type?: string | null;
  filament_color?: string | null;
}

export interface ApiModelTarget {
  machine_profile: string;
  print_profile: string;
  filament_profile: string | null;
  filament_id: number | null;
  material_provider?: string | null;
  material_ref?: string | null;
  filament_type: string;
  filament_color: string;
  filament_map: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
}

export interface ApiJob {
  id: number;
  uploaded_file_id: number;
  plate_number: number;
  order_id: number | null;
  project_id?: number | null;
  assigned_printer_id: number | null;
  queue_position: number | null;
  status: string;
  overrides: Record<string, string> | null;
  block_reason: string | null;
  // Actual values
  actual_filament_grams: number | null;
  actual_seconds: number | null;
  actual_filament_breakdown: Array<{ extruder_index: number; filament_profile: string | null; grams: number }> | null;
  deduction_skipped: boolean | null;
  /** Why filament usage was not recorded (spool tracking suspended / no starting weight); null otherwise. */
  deduction_note?: string | null;
  // Estimate values
  estimate_status: 'pending' | 'done' | 'failed' | null;
  estimate_seconds: number | null;
  estimate_filament_grams: number | null;
  estimate_filament_breakdown: Array<{ extruder_index: number; filament_profile: string | null; grams: number }> | null;
  estimate_preset_label: { printer_name: string; machine_profile: string; process_profile: string; filament_profiles: string[] } | null;
  created_at: string;
  updated_at: string;
  materials: string[];
  eligible_printers: Array<{ id: number; name: string }>;
  model_targets: ApiModelTarget[];
  low_stock_warning: LowStockWarning | null;
  filament_cost: number | null;
  not_before: string | null;   // UTC ISO: the queue won't start this job before then
  // Slicing cache (BIZ-189)
  save_slice: boolean;               // keep this job's slice in the library as a cached version
  save_slice_name: string | null;
  allow_cached_slice: boolean;       // print a matching cached version instead of slicing when claimed
  sliced_version_id: number | null;  // the cached version this job prints / printed
  slice_cache_info: SliceCacheInfo | null;
}

/** The latest slicing-cache decision for a job (+ the outcome of saving its slice), for the job-details debug view. */
export interface SliceCacheInfo {
  decision?: 'hit' | 'miss';
  reason?: string | null;
  at?: string;
  cache_key?: string | null;
  source_content_hash?: string | null;
  sliced_version_id?: number | null;
  cached_file_id?: number | null;
  cached_file_hash?: string | null;
  preset_content_hash_stored?: string | null;
  preset_content_hash_current?: string | null;
  slicer_version_stored?: string | null;
  slicer_version_current?: string | null;
  stale?: boolean | null;
  stale_reasons?: string[];
  gate?: 'laminus_down' | null;   // claimed on this version while Laminus was down
  policy?: 'use_latest' | 'pin_cached' | null;
  save?: {
    outcome: 'saved' | 'duplicate' | 'failed';
    sliced_version_id: number | null;
    cache_key: string | null;
    file_id: number | null;
    error: string | null;
    at: string;
  };
}

export interface LowStockWarning {
  spool_id: number;
  spool_label: string;
  remaining_g: number;
  needed_g: number;
  message: string;
}

export interface ApiSliceFailure {
  printer_id: number;
  print_profile: string;
  filament_profile: string | null;
  slice_error: string | null;
}

export interface ApiJobPrinterConfig {
  printer_id: number;
  printer_name: string;
  printer_type: string;
  print_profile: string;
  filament_profile: string | null;
  filament_id: number | null;
  material_provider?: string | null;
  material_ref?: string | null;
  filament_type: string | null;
  filament_color: string | null;
  tool_index: number | null;
  filament_map?: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
  slice_failed: boolean;
  slice_error: string | null;
  from_model_target: boolean;   // materialized from a make/model target rather than picked explicitly
  low_stock_warning: LowStockWarning | null;
}

export interface ApiJobDetails extends ApiJob {
  block_reason: string | null;
  file: { id: number; original_filename: string } | null;
  plate: { estimated_time: number | null; filament_g: number | null; thumbnail_path: string | null } | null;
  printer_configs: ApiJobPrinterConfig[];
  assigned_printer: { id: number; name: string; printer_type: string } | null;
  filament_grams_live: number | null;       // from live GcodeFile (slicing→printing only)
  estimated_seconds_live: number | null;    // from live GcodeFile (slicing→printing only)
}

/** Build the URL that serves a plate's embedded thumbnail via the files API. */
export function plateThumbnailUrl(fileId: number, thumbnailPath: string | null | undefined): string | null {
  if (!thumbnailPath) return null;
  const filename = thumbnailPath.replace(/\\/g, '/').split('/').pop();
  if (!filename) return null;
  return withKeyParam(`/api/v1/files/${fileId}/thumbnails/${filename}`);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

export async function uploadFile(file: File, folder?: string): Promise<ApiUploadedFile> {
  const body = new FormData();
  body.append('file', file);
  if (folder) body.append('folder', folder);
  const resp = await apiFetch('/api/v1/files/upload', { method: 'POST', body });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

export async function getFilePlates(fileId: number): Promise<ApiPlate[]> {
  const body = await request<{ filename: string; plates: ApiPlate[] }>(`/api/v1/files/${fileId}/plates`);
  return body.plates;
}

export async function getModelFilaments(fileId: number): Promise<ModelFilament[]> {
  return request(`/api/v1/files/${fileId}/model-filaments`);
}

export interface EmbeddedSetting {
  key: string;
  label: string;
  value: string;
}

export async function getEmbeddedSettings(fileId: number): Promise<EmbeddedSetting[]> {
  return request(`/api/v1/files/${fileId}/embedded-settings`);
}

export async function getPrinterProfiles(printerId: number): Promise<PrinterProfiles> {
  return request(`/api/v1/printers/${printerId}/profiles`);
}

export async function createJob(body: {
  uploaded_file_id: number;
  plate_number: number;
  printer_configs: PrinterConfigInput[];
  model_targets?: ModelTargetInput[];
  order_id?: number | null;
  project_id?: number | null;
  overrides?: Record<string, string> | null;
  save_slice?: boolean;
  save_slice_name?: string | null;
}): Promise<ApiJob> {
  return request('/api/v1/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Every job, in queue order (all statuses). */
export async function listJobs(): Promise<ApiJob[]> {
  return request('/api/v1/jobs');
}

/** Attach an existing job to a project, or detach it with null (a job moves between projects only via null first). */
export async function setJobProject(jobId: number, projectId: number | null): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/project`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId }),
  });
}

/** Flag a job to keep its slice in the library (saved right away if it's already sliced), or stop a pending save. */
export async function setJobSaveSlice(jobId: number, saveSlice: boolean, name?: string | null): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/save-slice`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ save_slice: saveSlice, name: name || null }),
  });
}

export interface OverrideChange { key: string; from: string; to: string; }
export interface OverrideCheck {
  has_embedded_settings: boolean;
  has_findings: boolean;
  setting_changes: OverrideChange[];
  slot_warning: { used_slots: number; printer_slots: number } | null;
}

export async function checkOverrides(body: {
  uploaded_file_id: number;
  printer_id: number;
  print_profile: string;
  filament_profile?: string | null;
  filament_color?: string | null;
}): Promise<OverrideCheck> {
  return request('/api/v1/jobs/check-overrides', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export interface QueueConfig {
  check_interval_minutes: number; operator_name: string | null; snapshot_interval_seconds: number;
  estimates_enabled: boolean;
  /** Slicing cache: reslice a cached version whose presets/OrcaSlicer changed (true) or still print it (false). */
  slice_cache_use_latest_settings: boolean;
}

export async function getQueueConfig(): Promise<QueueConfig> {
  return request('/api/v1/settings/queue');
}

export async function saveQueueConfig(body: Partial<QueueConfig>): Promise<QueueConfig> {
  return request('/api/v1/settings/queue', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function useQueueConfig(): { config: QueueConfig | null; refetch: () => void } {
  const [config, setConfig] = useState<QueueConfig | null>(null);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getQueueConfig()
      .then(data => { if (alive) setConfig(data); })
      .catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  return { config, refetch };
}

export async function getQueue(): Promise<ApiJob[]> {
  return request('/api/v1/queue');
}

export async function cancelJob(jobId: number): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/cancel`, { method: 'POST' });
}

export async function unblockJob(jobId: number): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/unblock`, { method: 'POST' });
}

export async function reorderJob(
  jobId: number,
  action: 'promote' | 'demote' | 'front' | 'back',
): Promise<ApiJob> {
  return request<ApiJob>(`/api/v1/jobs/${jobId}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
}

export async function updateJobConfigs(
  jobId: number,
  configs: PrinterConfigInput[],
  overrides?: Record<string, string> | null,
  modelTargets: ModelTargetInput[] = [],
): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/configs`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_configs: configs, model_targets: modelTargets, overrides: overrides ?? null }),
  });
}

export async function getJobDetails(jobId: number): Promise<ApiJobDetails> {
  return request(`/api/v1/jobs/${jobId}/details`);
}

export async function setJobCost(jobId: number, filamentCost: number | null): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/cost`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filament_cost: filamentCost }),
  });
}

export async function setJobSchedule(jobId: number, notBefore: string | null): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/schedule`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ not_before: notBefore }),
  });
}

export async function getSliceFailures(jobId: number): Promise<ApiSliceFailure[]> {
  return request(`/api/v1/jobs/${jobId}/slice-failures`);
}

export async function verifySlice(
  jobId: number,
  printerId: number,
): Promise<{ ok: boolean; error: string | null }> {
  return request(`/api/v1/jobs/${jobId}/verify-slice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId }),
  });
}

export async function completeJobManually(
  jobId: number,
  printerId: number,
): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/complete-manually`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_id: printerId }),
  });
}

export async function markJobOutcome(
  jobId: number,
  failures: { project_item_id: number; quantity_failed: number }[],
): Promise<{ failures: { project_item_id: number; quantity_failed: number }[] }> {
  return request(`/api/v1/jobs/${jobId}/outcome`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ failures }),
  });
}

export async function reorderQueue(
  positions: { job_id: number; queue_position: number }[],
): Promise<ApiJob[]> {
  return request('/api/v1/queue/reorder', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ positions }),
  });
}

export function useQueue(): { jobs: ApiJob[]; refetch: () => void } {
  const [jobs, setJobs] = useState<ApiJob[]>([]);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getQueue()
      .then(data => { if (alive) setJobs(data); })
      .catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  useEffect(() => {
    // After a reconnect, refetch: frames sent while the socket was down are gone.
    return openLiveSocket((e) => {
      try {
        const msg = JSON.parse(e.data) as { type: string; data: unknown };
        if (msg.type === 'queue_update' && Array.isArray(msg.data)) {
          // Merge status/position updates so enriched fields (materials, eligible_printers) survive
          const updates = msg.data as Array<{ id: number; status: string; queue_position: number | null }>;
          setJobs(prev => {
            const prevMap = new Map(prev.map(j => [j.id, j]));
            return updates.map(u => ({ ...(prevMap.get(u.id) ?? {} as ApiJob), ...u }));
          });
        } else if (msg.type === 'job_update') {
          const update = msg.data as ApiJob;
          setJobs(prev => {
            const idx = prev.findIndex(j => j.id === update.id);
            if (update.status === 'cancelled' || update.status === 'complete') {
              return prev.filter(j => j.id !== update.id);
            }
            if (idx === -1) return [...prev, update];
            return prev.map(j => (j.id === update.id ? { ...j, ...update } : j));
          });
        }
      } catch {
        // ignore malformed frames
      }
    }, refetch);
  }, [refetch]);

  return { jobs, refetch };
}

// Cache file plate metadata to avoid repeated fetches for the same file
const _plateCache = new Map<number, ApiPlate[]>();
const _filenameCache = new Map<number, string>();
const _plateCallbacks = new Map<number, Set<() => void>>();

export function useFilePlates(fileIds: number[]): {
  getPlate: (fileId: number, plateNumber: number) => ApiPlate | null;
  getFileName: (fileId: number) => string | null;
} {
  const [, setVersion] = useState(0);

  useEffect(() => {
    const unique = [...new Set(fileIds)].filter(id => !_plateCache.has(id));
    if (unique.length === 0) return;

    unique.forEach(id => {
      if (!_plateCallbacks.has(id)) {
        _plateCallbacks.set(id, new Set());
        request<{ filename: string; plates: ApiPlate[] }>(`/api/v1/files/${id}/plates`)
          .then(body => {
            _plateCache.set(id, body.plates);
            _filenameCache.set(id, body.filename);
            _plateCallbacks.get(id)?.forEach(cb => cb());
            _plateCallbacks.delete(id);
          })
          .catch(console.error);
      }
    });

    const bump = () => setVersion(v => v + 1);
    unique.forEach(id => _plateCallbacks.get(id)?.add(bump));
    return () => {
      unique.forEach(id => _plateCallbacks.get(id)?.delete(bump));
    };
  }, [fileIds.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    getPlate: (fileId: number, plateNumber: number) => {
      const plates = _plateCache.get(fileId);
      if (!plates) return null;
      return plates.find(p => p.plate_number === plateNumber) ?? null;
    },
    getFileName: (fileId: number) => _filenameCache.get(fileId) ?? null,
  };
}
