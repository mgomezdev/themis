import { createContext, useContext, useEffect } from 'react';
import { useLocation } from 'react-router-dom';

/** A breadcrumb: plain text, or a link to another screen. */
export type Crumb = string | { label: string; to: string };

export interface TopbarOverride {
  /** Pathname the override was set for — AppShell ignores it once the route changes. */
  path: string;
  title?: string;
  crumbs?: Crumb[];
}

export const TopbarOverrideContext = createContext<(o: TopbarOverride | null) => void>(() => {});

/**
 * Lets a screen replace the Topbar's static title/crumbs from `screenConfig` with ones built
 * from data it loaded (e.g. a project's name and customer). Pass `undefined` for both while
 * loading to keep the static config.
 */
export function useTopbarOverride(title: string | undefined, crumbs: Crumb[] | undefined) {
  const setOverride = useContext(TopbarOverrideContext);
  const { pathname } = useLocation();
  const key = JSON.stringify([title, crumbs]);
  useEffect(() => {
    if (title === undefined && crumbs === undefined) return;
    setOverride({ path: pathname, title, crumbs });
    return () => setOverride(null);
    // `key` stands in for title/crumbs so a new crumbs array with the same content doesn't re-fire.
  }, [setOverride, pathname, key]);
}
