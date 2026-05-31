import { useState, useEffect, useCallback } from 'react';
import type { StatusKey } from '../data/types';

export type OrderType = 'customer' | 'internal';

export interface ApiOrderPart {
  id: string;
  name: string;
  qty: number;
  material: string;
  est_minutes: number;
  filament_id: number | null;
  filament_color: string | null;
}

export interface OrderJobSummary {
  id: number;
  status: string;
  plate_number: number;
  uploaded_file_id: number;
  queue_position: number | null;
}

export interface ApiOrder {
  id: number;
  order_type: OrderType;
  customer: string;
  title: string;
  due_date: string | null;
  notes: string | null;
  on_hold: boolean;
  parts: ApiOrderPart[];
  status: StatusKey;
  progress: number;       // 0..1
  job_count: number;
  created_at: string;
  updated_at: string;
}

export interface ApiOrderDetail extends ApiOrder {
  jobs: OrderJobSummary[];
}

export interface OrderPartInput {
  id?: string;
  name: string;
  qty: number;
  material: string;
  est_minutes: number;
  filament_id?: number | null;
  filament_color?: string | null;
}

export interface OrderCreateInput {
  order_type: OrderType;
  customer: string;
  title: string;
  due_date: string | null;
  notes: string | null;
  parts: OrderPartInput[];
}

export type OrderPatchInput = Partial<OrderCreateInput & { on_hold: boolean }>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

export async function getOrders(): Promise<ApiOrder[]> {
  return request('/api/v1/orders');
}

export async function getOrder(id: number): Promise<ApiOrderDetail> {
  return request(`/api/v1/orders/${id}`);
}

export async function createOrder(body: OrderCreateInput): Promise<ApiOrderDetail> {
  return request('/api/v1/orders', jsonInit('POST', body));
}

export async function updateOrder(id: number, patch: OrderPatchInput): Promise<ApiOrderDetail> {
  return request(`/api/v1/orders/${id}`, jsonInit('PATCH', patch));
}

export async function deleteOrder(id: number): Promise<void> {
  const resp = await fetch(`/api/v1/orders/${id}`, { method: 'DELETE' });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
}

/** Orders list that refetches when jobs change (so derived progress stays live). */
export function useOrders(): { orders: ApiOrder[]; refetch: () => void } {
  const [orders, setOrders] = useState<ApiOrder[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getOrders().then(d => { if (alive) setOrders(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  useEffect(() => {
    let alive = true;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as { type: string };
        // Order progress is derived server-side, so refetch rather than patch locally.
        if (msg.type === 'job_update' || msg.type === 'queue_update') {
          getOrders().then(d => { if (alive) setOrders(d); }).catch(() => {});
        }
      } catch { /* ignore malformed frames */ }
    };
    return () => { alive = false; ws.close(); };
  }, []);

  return { orders, refetch };
}
