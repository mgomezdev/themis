// frontend/src/api/tags.ts
import { useCallback, useEffect, useState } from 'react';

export interface Tag {
  id: number;
  name: string;
  color: string;
  category: string;
  usage_count: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getTags = () => request<Tag[]>('/api/v1/tags');
export const createTag = (b: { name: string; color: string; category: string }) =>
  request<Tag>('/api/v1/tags', jsonInit('POST', b));
export const updateTag = (id: number, b: Partial<Pick<Tag, 'name' | 'color' | 'category'>>) =>
  request<Tag>(`/api/v1/tags/${id}`, jsonInit('PATCH', b));
export const deleteTag = (id: number) =>
  request<{ deleted: number }>(`/api/v1/tags/${id}`, { method: 'DELETE' });

export function useTags(): { tags: Tag[]; refetch: () => void } {
  const [tags, setTags] = useState<Tag[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getTags().then(d => { if (alive) setTags(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { tags, refetch };
}
