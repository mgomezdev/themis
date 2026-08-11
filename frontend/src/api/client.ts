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
