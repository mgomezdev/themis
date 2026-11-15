import { apiFetch } from '../api/client';

export type Role = 'admin' | 'staff' | 'customer' | null;

export interface SessionInfo {
  local: boolean;
  role: Role;
  customer: { id: number; name: string; email: string } | null;
}

export async function getSession(): Promise<SessionInfo | null> {
  try {
    const r = await apiFetch('/api/v1/auth/me');
    if (!r.ok) return null;
    const data = await r.json();
    return data && typeof data === 'object' && 'role' in data ? (data as SessionInfo) : null;
  } catch {
    return null;
  }
}

/** Customer email or the admin username. Returns the session key on success, or an error message. */
export async function login(email: string, password: string): Promise<{ key: string } | { error: string }> {
  try {
    const r = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (r.status === 401) return { error: 'Invalid email or password' };
    if (!r.ok) return { error: await errorFrom(r) };
    const data = await r.json();
    return typeof data?.key === 'string' ? { key: data.key } : { error: 'Server unreachable' };
  } catch {
    return { error: 'Server unreachable' };
  }
}

/** Asks the server to write a one-time admin recovery code to its log. */
export async function requestRecoveryCode(): Promise<boolean> {
  try {
    const r = await fetch('/api/v1/auth/recover', { method: 'POST' });
    return r.status === 202;
  } catch {
    return false;
  }
}

/** true on success, else an error message. */
export async function confirmRecovery(code: string, password: string): Promise<true | string> {
  try {
    const r = await fetch('/api/v1/auth/recover/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, password }),
    });
    if (r.ok) return true;
    // 400 bad code, 422 password too short, 429 throttled: show the server's reason.
    return await errorFrom(r, r.status === 400 ? 'Invalid or expired recovery code' : undefined);
  } catch {
    return 'Server unreachable';
  }
}

/** A failed auth response's message: the server's string `detail`, else `fallback`, else by status. */
async function errorFrom(r: Response, fallback?: string): Promise<string> {
  const detail = await r.json().then((d) => d?.detail, () => null);
  if (typeof detail === 'string' && detail) return detail;
  if (fallback) return fallback;
  return r.status >= 500 ? 'Server unreachable' : `Request failed (${r.status})`;
}
