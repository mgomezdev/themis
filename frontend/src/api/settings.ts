const BASE = '/api/v1/settings';

export interface FleetImportReport {
  imported: number;
  skipped: number;
  warnings: string[];
}

export async function downloadFleetBackup(): Promise<void> {
  const resp = await fetch(`${BASE}/fleet-backup`);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'themis-fleet-backup.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function importFleetBackup(file: File): Promise<FleetImportReport> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${BASE}/fleet-import`, { method: 'POST', body: form });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}
