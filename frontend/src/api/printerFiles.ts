import { apiFetch } from './client';

const BASE = '/api/v1/printers';

export interface PrinterFileMeta {
  estimated_seconds?: number;
  filament_grams?: number;
  filament_mm?: number;
  slicer?: string;
}

export interface PrinterFileEntry {
  id: string;                  // pass back to print / delete / to-library / as `directory`
  name: string;
  size: number;
  modified_at: string | null;
  is_dir: boolean;
  metadata: PrinterFileMeta | null;
  printable: boolean;
}

export interface PrinterFilesListing {
  printer_id: number;
  directory: string;
  files: PrinterFileEntry[];
  can_delete: boolean;
  can_download: boolean;
}

export interface MergedPrinterFiles {
  printer_id: number;
  printer_name: string;
  files: PrinterFileEntry[];
  error: string | null;
  can_delete: boolean;
  can_download: boolean;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.json())?.detail ?? ''; } catch { /* not JSON */ }
    throw new Error(detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

export async function listAllPrinterFiles(): Promise<MergedPrinterFiles[]> {
  const body = await request<{ printers: MergedPrinterFiles[] }>(`${BASE}/files/all`);
  return Array.isArray(body?.printers) ? body.printers : [];
}

export function listPrinterFiles(printerId: number, directory = '/'): Promise<PrinterFilesListing> {
  return request(`${BASE}/${printerId}/files?directory=${encodeURIComponent(directory)}`);
}

export function printStoredFile(printerId: number, fileId: string): Promise<{ ok: boolean }> {
  return request(`${BASE}/${printerId}/files/print`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: fileId }),
  });
}

export function deleteStoredFile(printerId: number, fileId: string): Promise<{ ok: boolean }> {
  return request(`${BASE}/${printerId}/files?file_id=${encodeURIComponent(fileId)}`, { method: 'DELETE' });
}

export function copyStoredFileToLibrary(printerId: number, fileId: string): Promise<{ id: number; original_filename: string }> {
  return request(`${BASE}/${printerId}/files/to-library`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: fileId }),
  });
}
