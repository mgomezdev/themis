// Deliberately does NOT use apiFetch (api/client.ts) - this is called from
// SharedProjectScreen, which has no API key and must not touch the authenticated
// client's 401/403 handling. See docs/agent/conventions.md § Invariants.

export interface PublicProjectItem {
  name: string;
  quantity: number;
  quantity_completed: number;
}

export interface PublicProjectPart {
  name: string;
  quantity: number;
}

export interface PublicProjectLink {
  url: string;
  label: string | null;
}

export interface PublicProject {
  name: string;
  customer: string;
  due_date: string | null;
  on_hold: boolean;
  items: PublicProjectItem[];
  parts: PublicProjectPart[];
  links: PublicProjectLink[];
  jobs_total: number;
  jobs_complete: number;
  estimate_seconds_remaining: number | null;
  updated_at: string;
}

export async function getPublicProject(token: string): Promise<PublicProject> {
  const resp = await fetch(`/api/v1/public/projects/${encodeURIComponent(token)}`);
  if (!resp.ok) {
    throw new Error(resp.status === 404 ? 'not-found' : `${resp.status}`);
  }
  return resp.json();
}
