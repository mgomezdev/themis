import { useState, useEffect, useCallback } from 'react';

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

export interface PrinterConfigInput {
  printer_id: number;
  print_profile: string;
  filament_profile?: string | null;
  filament_id?: number | null;
  filament_type?: string | null;
  filament_color?: string | null;
  tool_index?: number | null;
}

export interface ApiJob {
  id: number;
  uploaded_file_id: number;
  plate_number: number;
  order_id: number | null;
  assigned_printer_id: number | null;
  queue_position: number | null;
  status: string;
  block_reason: string | null;
  created_at: string;
  updated_at: string;
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
  filament_type: string | null;
  filament_color: string | null;
  slice_failed: boolean;
  slice_error: string | null;
}

export interface ApiJobDetails extends ApiJob {
  block_reason: string | null;
  file: { id: number; original_filename: string } | null;
  plate: { estimated_time: number | null; filament_g: number | null; thumbnail_path: string | null } | null;
  printer_configs: ApiJobPrinterConfig[];
  assigned_printer: { id: number; name: string; printer_type: string } | null;
}

/** Build the URL that serves a plate's embedded thumbnail via the files API. */
export function plateThumbnailUrl(fileId: number, thumbnailPath: string | null | undefined): string | null {
  if (!thumbnailPath) return null;
  const filename = thumbnailPath.replace(/\\/g, '/').split('/').pop();
  if (!filename) return null;
  return `/api/v1/files/${fileId}/thumbnails/${filename}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
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
  const resp = await fetch('/api/v1/files/upload', { method: 'POST', body });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

export async function getFilePlates(fileId: number): Promise<ApiPlate[]> {
  return request(`/api/v1/files/${fileId}/plates`);
}

export async function getPrinterProfiles(printerId: number): Promise<PrinterProfiles> {
  return request(`/api/v1/printers/${printerId}/profiles`);
}

export async function createJob(body: {
  uploaded_file_id: number;
  plate_number: number;
  printer_configs: PrinterConfigInput[];
  order_id?: number | null;
}): Promise<ApiJob> {
  return request('/api/v1/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export interface QueueConfig { check_interval_minutes: number; }

export async function getQueueConfig(): Promise<QueueConfig> {
  return request('/api/v1/settings/queue');
}

export async function saveQueueConfig(body: QueueConfig): Promise<QueueConfig> {
  return request('/api/v1/settings/queue', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
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

export async function updateJobConfigs(
  jobId: number,
  configs: PrinterConfigInput[],
): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/configs`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_configs: configs }),
  });
}

export async function getJobDetails(jobId: number): Promise<ApiJobDetails> {
  return request(`/api/v1/jobs/${jobId}/details`);
}

export async function getSliceFailures(jobId: number): Promise<ApiSliceFailure[]> {
  return request(`/api/v1/jobs/${jobId}/slice-failures`);
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
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as { type: string; data: unknown };
        if (msg.type === 'queue_update' && Array.isArray(msg.data)) {
          // Full queue replacement from server broadcast
          setJobs(msg.data as ApiJob[]);
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
    };
    return () => { ws.close(); };
  }, []);

  return { jobs, refetch };
}

// Cache file plate metadata to avoid repeated fetches for the same file
const _plateCache = new Map<number, ApiPlate[]>();
const _plateCallbacks = new Map<number, Set<() => void>>();

export function useFilePlates(fileIds: number[]): (fileId: number, plateNumber: number) => ApiPlate | null {
  const [, setVersion] = useState(0);

  useEffect(() => {
    const unique = [...new Set(fileIds)].filter(id => !_plateCache.has(id));
    if (unique.length === 0) return;

    unique.forEach(id => {
      if (!_plateCallbacks.has(id)) {
        _plateCallbacks.set(id, new Set());
        getFilePlates(id)
          .then(plates => {
            _plateCache.set(id, plates);
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

  return (fileId: number, plateNumber: number) => {
    const plates = _plateCache.get(fileId);
    if (!plates) return null;
    return plates.find(p => p.plate_number === plateNumber) ?? null;
  };
}
