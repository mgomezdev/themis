import { apiFetch } from './client';

export interface AdminAccount {
  username: string;
  password_set: boolean;
  allow_local_login: boolean;
  /** API keys (not login sessions) that can manage keys — still work when sign-in is required. */
  full_access_keys: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(typeof body?.detail === 'string' ? body.detail : `${resp.status}`);
  }
  return resp.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getAdminAccount = () => request<AdminAccount>('/api/v1/admin-account');
export const setAdminPassword = (password: string) =>
  request<AdminAccount>('/api/v1/admin-account/password', json('PUT', { password }));
export const setAllowLocalLogin = (allow_local_login: boolean) =>
  request<AdminAccount>('/api/v1/admin-account', json('PATCH', { allow_local_login }));
