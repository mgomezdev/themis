import { getApiKey } from '../auth/apiKeyStore';

let unauthorizedHandler: (() => void) | null = null;

/** Registered by AuthGate on mount; called whenever an apiFetch response comes back 401
 *  (this browser's key was revoked/deleted elsewhere), so the gate can clear the stored
 *  key and fall back to the manual-entry form without every call site duplicating the check. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const key = getApiKey();
  const headers = new Headers(init?.headers);
  if (key) headers.set('X-Api-Key', key);
  const resp = await fetch(url, { ...init, headers });
  if (resp.status === 401) unauthorizedHandler?.();
  return resp;
}

export function withKeyParam(url: string): string {
  const key = getApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}key=${encodeURIComponent(key)}`;
}

/** Opens an authenticated /ws connection (key carried as ?key=, the one endpoint that can't take
 *  a header). Shared by every hook that opens its own /ws socket (queue/orders/fleet). */
export function openAuthedWebSocket(): WebSocket {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const key = getApiKey();
  return new WebSocket(`${proto}//${window.location.host}/ws?key=${encodeURIComponent(key ?? '')}`);
}
