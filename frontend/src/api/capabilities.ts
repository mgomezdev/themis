import { useCallback, useEffect, useState } from 'react';
import { pluginRequest, usePlugins, type CapabilityRef } from './plugins';

export type CapabilityStatus = 'serving' | 'waiting' | 'error' | 'disabled' | 'none_selected' | 'no_provider' | 'dormant';

export interface CapabilityProvider {
  plugin_id: string; name: string; version: number; enabled: boolean;
  status: CapabilityStatus | 'not_selected'; waiting_on: string[];
}

export interface CapabilityInfo {
  id: string; version: number; label: string; description: string;
  /** The plugin that defines it; null for a core capability. */
  definer: string | null;
  features: string[]; required_methods: string[];
  selected: string | null; explicit: boolean;
  status: CapabilityStatus; waiting_on: string[]; error: string | null;
  providers: CapabilityProvider[];
  requires_by: { plugin_id: string; min_version: number }[];
}
export type { CapabilityRef };

export const fetchCapabilities = async (): Promise<CapabilityInfo[]> =>
  (await pluginRequest<{ capabilities: CapabilityInfo[] }>('/api/v1/capabilities')).capabilities;

/** Every known capability. Refetches when the plugin list changes (a provider switch, enable toggle, install). */
export function useCapabilityList(): { items: CapabilityInfo[]; loaded: boolean; error: string | null; reload: () => void } {
  const { plugins, selections } = usePlugins();
  const [state, setState] = useState<{ items: CapabilityInfo[]; loaded: boolean; error: string | null }>({ items: [], loaded: false, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    fetchCapabilities()
      .then(items => { if (alive) setState({ items, loaded: true, error: null }); })
      .catch(e => { if (alive) setState({ items: [], loaded: true, error: e instanceof Error ? e.message : String(e) }); });
    return () => { alive = false; };
  }, [plugins, selections, tick]);
  const reload = useCallback(() => setTick(t => t + 1), []);
  return { ...state, reload };
}
