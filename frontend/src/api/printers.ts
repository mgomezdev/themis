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
  filament_id: string | null;
  name: string;
  type: string;
  color: string;
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
  connected: boolean;
  loaded_filaments: LoadedFilament[];
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
  loaded_filaments?: LoadedFilament[];
}

export function fetchPrinterTypes(): Promise<PrinterType[]> {
  return request(`${BASE}/types`);
}

export function fetchPrinters(): Promise<ApiPrinter[]> {
  return request(BASE);
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
