const BASE = '/api/v1/settings';

export interface WebhookConfig {
  url: string | null;
  secret: string | null;
  events: string[];
}

export async function getWebhookConfig(): Promise<WebhookConfig> {
  const resp = await fetch(`${BASE}/webhook`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export async function saveWebhookConfig(cfg: Partial<WebhookConfig>): Promise<WebhookConfig> {
  const resp = await fetch(`${BASE}/webhook`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

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
