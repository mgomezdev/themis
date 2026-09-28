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

/** Returns the session key on success, or an error message. */
export async function loginCustomer(email: string, password: string): Promise<{ key: string } | { error: string }> {
  try {
    const r = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (r.status === 401) return { error: 'Invalid email or password' };
    if (!r.ok) return { error: 'Server unreachable' };
    const data = await r.json();
    return typeof data?.key === 'string' ? { key: data.key } : { error: 'Server unreachable' };
  } catch {
    return { error: 'Server unreachable' };
  }
}
