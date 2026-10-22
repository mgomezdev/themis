import type { ApiPrinter } from '../api/printers';
import type { ModelTargetInput, PrinterConfigInput } from '../api/queue';
import type { PerPrinterCfg } from './PerPrinterConfig';

// "Any printer of this model" selections ride in the same selection list as specific printers, keyed
// `model:<model uuid>` (a printer's make/model is its registry model, BIZ-294).
const MODEL_PREFIX = 'model:';

export const modelKey = (modelUuid: string): string => `${MODEL_PREFIX}${modelUuid}`;
export const isModelKey = (key: string): boolean => key.startsWith(MODEL_PREFIX);
export const modelUuidOf = (key: string): string => key.slice(MODEL_PREFIX.length);

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
  const uuid = modelUuidOf(key);
  return printers.find(p => p.model_uuid === uuid)?.model_label ?? fallback ?? 'this model';
}

/**
 * What PerPrinterConfig needs to configure a model target: a representative printer (to list the profiles the
 * model supports) presented as "Any <model>" with no loaded slots (slot/tool picks are printer-specific).
 * Null when no printer of that model exists right now.
 */
export function modelConfigSource(key: string, printers: ApiPrinter[]): { printerId: string; printers: ApiPrinter[] } | null {
  const uuid = modelUuidOf(key);
  const rep = printers.find(p => p.model_uuid === uuid);
  if (!rep) return null;
  return {
    printerId: String(rep.id),
    printers: [{ ...rep, name: `Any ${rep.model_label ?? 'printer of this model'}`, loaded_filaments: [] }],
  };
}

export function ModelPicker({ printers, selected, onToggle }: {
  printers: ApiPrinter[];
  selected: string[];
  onToggle: (key: string) => void;
}) {
  const groups = modelGroups(printers);
  if (groups.size === 0) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div className="tiny muted" style={{ marginBottom: 6 }}>
        Or any printer of a model — including ones added later:
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
        {[...groups.entries()].map(([uuid, members]) => {
          const key = modelKey(uuid);
          const on = selected.includes(key);
          const label = members[0].model_label ?? 'printer of this model';
          return (
            <button
              key={key}
              data-testid={`model-target-${uuid}`}
              onClick={() => onToggle(key)}
              style={{
                padding: 12, textAlign: 'left', cursor: 'pointer', borderRadius: 10, fontFamily: 'inherit',
                background: on ? 'var(--bg-3)' : 'var(--bg-1)', color: 'var(--text-1)',
                border: `1px solid ${on ? 'var(--accent)' : 'var(--border-1)'}`,
                boxShadow: on ? '0 0 0 1px var(--accent)' : 'none',
              }}>
              <div className="small" style={{ fontWeight: 500 }}>Any {label}</div>
              <div className="tiny muted">{members.length} printer{members.length === 1 ? '' : 's'} now</div>
            </button>
          );
        })}
      </div>
    </div>
  );
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
