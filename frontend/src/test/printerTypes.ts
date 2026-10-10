import type { ConnectionField, PrinterType } from '../api/printers';

/** A model entry as `GET /printers/types` returns it; defaults describe an enabled single-toolhead printer. */
export function printerType(over: Partial<PrinterType> & Pick<PrinterType, 'plugin_id'>): PrinterType {
  return {
    manufacturer_id: over.plugin_id,
    manufacturer_name: over.plugin_id,
    model_id: 'm1',
    display_name: 'Model 1',
    bed_mm: [256, 256],
    toolheads: 1,
    connection_fields: [],
    plugin_enabled: true,
    ...over,
  };
}

export const IP_FIELD: ConnectionField = {
  name: 'ip_address', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '', help_text: '',
};
