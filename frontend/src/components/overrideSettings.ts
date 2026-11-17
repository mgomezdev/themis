import { getOverrideSettings, type OverrideSetting } from '../api/queue';

let settingsPromise: Promise<OverrideSetting[]> | null = null;

/** The overridable settings, fetched once per page load (a failed fetch is retried next time). */
export function loadOverrideSettings(): Promise<OverrideSetting[]> {
  if (!settingsPromise) {
    settingsPromise = Promise.resolve().then(() => getOverrideSettings())
      .then(s => { if (!Array.isArray(s)) throw new Error('bad override settings'); return s; })
      .catch(e => { settingsPromise = null; throw e; });
  }
  return settingsPromise;
}

/** Test hook: forget the cached settings list. */
export function resetOverrideSettingsCache() { settingsPromise = null; }
