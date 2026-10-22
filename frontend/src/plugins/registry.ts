import type { ComponentType } from 'react';
import { MaterialMappingsPage } from '../screens/MaterialMappingsPage';

/** `component` tabs: React components compiled into Themis, keyed by the `component` a bundled plugin's manifest declares
 *  for the tab (the manifest also declares the capability feature the tab `requires`). Only bundled plugins can use
 *  them (an installed plugin gets the `default` and `schema` renderers, which need no frontend code). */
export const COMPONENT_TABS: Record<string, ComponentType> = {
  'material-mappings': MaterialMappingsPage,
};
