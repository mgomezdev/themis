import { Navigate, useLocation } from 'react-router-dom';
import { usePlugins, type PluginSummary } from '../api/plugins';

/** Where an old absolute app URL now lives, from the redirects the plugins themselves declare (null = no plugin claims it). */
export function pluginRedirectFor(plugins: PluginSummary[], pathname: string): string | null {
  for (const p of plugins) {
    const hit = p.ui.redirects?.find(r => r.from === pathname);
    if (hit) return `/plugins/${p.id}/${hit.tab}`;
  }
  return null;
}

/** Renders a redirect when the current URL is one a plugin declared as moved; nothing otherwise. */
export function PluginRedirects() {
  const { pathname } = useLocation();
  const { plugins } = usePlugins();
  const to = pluginRedirectFor(plugins, pathname);
  return to ? <Navigate to={to} replace /> : null;
}
