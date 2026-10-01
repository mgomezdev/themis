import { apiFetch } from './client';

export const PAYMENT_METHODS = [
  { value: 'cash', label: 'Cash' },
  { value: 'card', label: 'Card' },
  { value: 'bank_transfer', label: 'Bank transfer' },
  { value: 'check', label: 'Check' },
  { value: 'other', label: 'Other' },
] as const;
export type PaymentMethod = typeof PAYMENT_METHODS[number]['value'];

export const methodLabel = (m: string): string => PAYMENT_METHODS.find(p => p.value === m)?.label ?? m;

export interface ProjectPayment {
  id: number;
  project_id: number;
  amount: number;
  received_on: string;        // YYYY-MM-DD, the day the money arrived
  method: PaymentMethod;
  note: string | null;
  created_at: string;
}

/** A payment in a customer's cross-project history (`GET /customers/{id}/payments`). */
export interface CustomerPayment extends ProjectPayment {
  project_name: string;
}

export interface NewPayment {
  amount: number;
  received_on?: string;
  method: PaymentMethod;
  note?: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await apiFetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = body?.detail;
    // FastAPI validation errors are a list of {msg}; everything else is a string.
    throw new Error(typeof detail === 'string' ? detail
      : Array.isArray(detail) && detail[0]?.msg ? String(detail[0].msg) : `${resp.status}`);
  }
  return resp.json();
}

export const listPayments = (projectId: number) =>
  request<ProjectPayment[]>(`/api/v1/projects/${projectId}/payments`);

export const addPayment = (projectId: number, body: NewPayment) =>
  request<ProjectPayment>(`/api/v1/projects/${projectId}/payments`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });

export const deletePayment = (projectId: number, paymentId: number) =>
  request<{ deleted: number }>(`/api/v1/projects/${projectId}/payments/${paymentId}`, { method: 'DELETE' });

export const getCustomerPayments = (customerId: number) =>
  request<CustomerPayment[]>(`/api/v1/customers/${customerId}/payments`);
