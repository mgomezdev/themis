import { useEffect, useState } from 'react';
import { apiFetch } from './client';

export interface BuildInfo {
  version: string;
  git_sha: string;
}

export const UNKNOWN_SHA = 'unknown';

/** Short form of a commit sha for display (7 chars, the git default); non-shas pass through. */
export function shortSha(sha: string): string {
  return /^[0-9a-f]{40}$/i.test(sha) ? sha.slice(0, 7) : sha;
}

/** Build identity of the running *server*, from the public health probe. Null until loaded or on failure. */
export function useBuildInfo(): BuildInfo | null {
  const [info, setInfo] = useState<BuildInfo | null>(null);

  useEffect(() => {
    let alive = true;
    apiFetch('/api/v1/health')
      .then(r => r.json())
      .then((d: Partial<BuildInfo>) => {
        if (alive && typeof d.version === 'string' && typeof d.git_sha === 'string') {
          setInfo({ version: d.version, git_sha: d.git_sha });
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  return info;
}
