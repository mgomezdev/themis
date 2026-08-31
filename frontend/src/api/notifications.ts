import { apiFetch } from './client';

const BASE = '/api/v1/settings';

export interface NtfyConfig {
  enabled: boolean;
  server_url: string | null;
  topic: string | null;
  priority?: number | null;
  events: string[];
}

export interface DiscordConfig {
  enabled: boolean;
  webhook_url: string | null;
  events: string[];
}

export interface EmailConfig {
  enabled: boolean;
  host: string | null;
  port: number | null;
  username: string | null;
  password: string | null;
  from_addr: string | null;
  to_addrs: string[];
  events: string[];
}

export interface NotificationConfig {
  ntfy: NtfyConfig;
  discord: DiscordConfig;
  email: EmailConfig;
}

export async function getNotificationConfig(): Promise<NotificationConfig> {
  const resp = await apiFetch(`${BASE}/notifications`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export async function saveNotificationConfig(cfg: NotificationConfig): Promise<NotificationConfig> {
  const resp = await apiFetch(`${BASE}/notifications`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export async function testNotificationChannel(
  channel: 'ntfy' | 'discord' | 'email',
  config: NtfyConfig | DiscordConfig | EmailConfig,
): Promise<{ ok: boolean; message: string }> {
  const resp = await apiFetch(`${BASE}/notifications/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, config }),
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}
