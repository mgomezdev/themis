import type { ComponentType } from 'react';
import { MaterialMappingsPage } from '../screens/MaterialMappingsPage';

/** `component` tabs: React components compiled into Themis, keyed `${plugin_id}/${tab_id}`. Only bundled plugins can use
 *  them (an installed plugin gets the `default` and `schema` renderers, which need no frontend code). */
export const COMPONENT_TABS: Record<string, ComponentType> = {
  'spoolman/mappings': MaterialMappingsPage,
};

/** Old settings URLs and where they live now. */
export const LEGACY_REDIRECTS: Record<string, string> = {
  '/settings/spoolman': '/plugins/spoolman/connection',
  '/settings/spoolman-mappings': '/plugins/spoolman/mappings',
};
