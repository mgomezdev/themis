import type { ApiPrinter } from '../api/printers';
import type { ModelTargetInput, PrinterConfigInput } from '../api/queue';
import type { PerPrinterCfg } from './PerPrinterConfig';

// Group selections ride in the same selection list as specific printers (`<printer id>`): "any printer of this model" is
// `model:<model uuid>` (a printer's make/model is its registry model, BIZ-294) and "any printer at all" is `any` (BIZ-357).
const MODEL_PREFIX = 'model:';
export const ANY_KEY = 'any';

export const modelKey = (modelUuid: string | null): string => (modelUuid == null ? ANY_KEY : `${MODEL_PREFIX}${modelUuid}`);
/** A group of printers (Any printer, or any printer of a model) rather than one specific printer. */
export const isModelKey = (key: string): boolean => key === ANY_KEY || key.startsWith(MODEL_PREFIX);
export const modelUuidOf = (key: string): string | null => (key === ANY_KEY ? null : key.slice(MODEL_PREFIX.length));

/** The printers a selection key covers right now. */
export function printersOfKey(key: string, printers: ApiPrinter[]): ApiPrinter[] {
  if (key === ANY_KEY) return printers;
  if (!isModelKey(key)) return printers.filter(p => String(p.id) === key);
  const uuid = modelUuidOf(key);
  return printers.filter(p => p.model_uuid === uuid);
}

/** Printers grouped by registry model; a printer with no registered model has no make/model to match on. */
export function modelGroups(printers: ApiPrinter[]): Map<string, ApiPrinter[]> {
  const groups = new Map<string, ApiPrinter[]>();
  for (const p of printers) {
    if (!p.model_uuid) continue;
    groups.set(p.model_uuid, [...(groups.get(p.model_uuid) ?? []), p]);
  }
  return groups;
}

/** "Bambu Lab P1S" for a model key, from the printers of that model; `fallback` (e.g. a stored target's label) when none exist now. */
export function modelLabelOf(key: string, printers: ApiPrinter[], fallback?: string | null): string {
  if (key === ANY_KEY) return 'printer';
  const uuid = modelUuidOf(key);
  return printers.find(p => p.model_uuid === uuid)?.model_label ?? fallback ?? 'this model';
}

/**
 * What PerPrinterConfig needs to configure a model target: a representative printer (to list the profiles the
 * model supports) presented as "Any <model>" with no loaded slots (slot/tool picks are printer-specific).
 * Null when no printer of that model exists right now.
 */
export function modelConfigSource(key: string, printers: ApiPrinter[]): { printerId: string; printers: ApiPrinter[] } | null {
  const rep = printersOfKey(key, printers)[0];
  if (!rep) return null;
  const name = key === ANY_KEY ? 'Any printer' : `Any ${rep.model_label ?? 'printer of this model'}`;
  return { printerId: String(rep.id), printers: [{ ...rep, name, loaded_filaments: [] }] };
}

/** Split a mixed selection into the API's explicit printer configs and make/model targets. */
export function buildEligibility(
  selected: string[],
  perPrinter: Record<string, PerPrinterCfg>,
): { printer_configs: PrinterConfigInput[]; model_targets: ModelTargetInput[] } {
  const printer_configs: PrinterConfigInput[] = [];
  const model_targets: ModelTargetInput[] = [];
  for (const key of selected) {
    const pp = perPrinter[key];
    // null = undefined = matches anything; a type always carries a subtype (the editor guarantees it, the server enforces it).
    const ask = {
      print_profile: pp.printProfile ?? '',   // gcode jobs have none
      filament_profile: pp.filamentProfile ?? null,
      filament_manufacturer: pp.filamentManufacturer ?? null,
      filament_type: pp.filamentType ?? null,
      filament_subtype: pp.filamentSubtype ?? null,
      filament_color: pp.filamentColor ?? null,
    };
    if (isModelKey(key)) {
      model_targets.push({ model_uuid: modelUuidOf(key), ...ask });
    } else {
      printer_configs.push({
        printer_id: Number(key), ...ask,
        tool_index: pp.toolIndex ?? null,
        filament_map: pp.filamentMap ?? null,
      });
    }
  }
  return { printer_configs, model_targets };
}
