import type { ComponentType } from 'react';
import { MaterialMappingsPage } from '../screens/MaterialMappingsPage';

/** `component` tabs: React components compiled into Themis, keyed `${plugin_id}/${tab_id}`. Only bundled plugins can use
 *  them (an installed plugin gets the `default` and `schema` renderers, which need no frontend code). */
export interface ComponentTab { Component: ComponentType; /** The tab only exists for a plugin that declares this capability. */ requires?: string }

export const COMPONENT_TABS: Record<string, ComponentTab> = {
  'spoolman/mappings': { Component: MaterialMappingsPage, requires: 'PROFILE_LINKS_READ' },
};

/** Old settings URLs and where they live now. */
export const LEGACY_REDIRECTS: Record<string, string> = {
  '/settings/spoolman': '/plugins/spoolman/connection',
  '/settings/spoolman-mappings': '/plugins/spoolman/mappings',
};
