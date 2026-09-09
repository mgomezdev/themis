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

// Thrown specifically for a 404 (invalid/revoked/never-existed token) so callers can
// tell "this link doesn't exist" apart from "the request failed" - a 500 or a network
// error is not the same thing as a revoked link and shouldn't be shown as one.
export class ShareNotFoundError extends Error {}

export async function getPublicProject(token: string): Promise<PublicProject> {
  const resp = await fetch(`/api/v1/public/projects/${encodeURIComponent(token)}`);
  if (!resp.ok) {
    if (resp.status === 404) throw new ShareNotFoundError('not-found');
    throw new Error(`${resp.status}`);
  }
  return resp.json();
}
