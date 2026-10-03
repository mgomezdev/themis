import type { ApiPrinter } from '../api/printers';
import type { ModelTargetInput, PrinterConfigInput } from '../api/queue';
import type { PerPrinterCfg } from './PerPrinterConfig';

// "Any printer of this make/model" selections ride in the same selection list as specific printers, keyed
// `model:<machine profile>` (a printer's make/model is its active OrcaSlicer machine preset).
const MODEL_PREFIX = 'model:';

export const modelKey = (profile: string): string => `${MODEL_PREFIX}${profile}`;
export const isModelKey = (key: string): boolean => key.startsWith(MODEL_PREFIX);
export const machineProfileOf = (key: string): string => key.slice(MODEL_PREFIX.length);

/** Printers grouped by machine preset; printers with no preset have no make/model to match on. */
export function modelGroups(printers: ApiPrinter[]): Map<string, ApiPrinter[]> {
  const groups = new Map<string, ApiPrinter[]>();
  for (const p of printers) {
    const profile = p.current_orca_printer_profile;
    if (!profile) continue;
    groups.set(profile, [...(groups.get(profile) ?? []), p]);
  }
  return groups;
}

/**
 * What PerPrinterConfig needs to configure a model target: a representative printer (to list the profiles the
 * model supports) presented as "Any <model>" with no loaded slots (slot/tool picks are printer-specific).
 * Null when no printer of that model exists right now.
 */
export function modelConfigSource(key: string, printers: ApiPrinter[]): { printerId: string; printers: ApiPrinter[] } | null {
  const profile = machineProfileOf(key);
  const rep = printers.find(p => p.current_orca_printer_profile === profile);
  if (!rep) return null;
  return {
    printerId: String(rep.id),
    printers: [{ ...rep, name: `Any ${profile}`, loaded_filaments: [] }],
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
        {[...groups.entries()].map(([profile, members]) => {
          const key = modelKey(profile);
          const on = selected.includes(key);
          return (
            <button
              key={key}
              data-testid={`model-target-${profile}`}
              onClick={() => onToggle(key)}
              style={{
                padding: 12, textAlign: 'left', cursor: 'pointer', borderRadius: 10, fontFamily: 'inherit',
                background: on ? 'var(--bg-3)' : 'var(--bg-1)', color: 'var(--text-1)',
                border: `1px solid ${on ? 'var(--accent)' : 'var(--border-1)'}`,
                boxShadow: on ? '0 0 0 1px var(--accent)' : 'none',
              }}>
              <div className="small" style={{ fontWeight: 500 }}>Any {profile}</div>
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
    // "any" is the wire form of "no preference" — the backend rejects null/blank here.
    const ask = {
      print_profile: pp.printProfile!,
      filament_profile: pp.filamentProfile ?? null,
      filament_id: pp.filamentId ?? null,
      filament_type: pp.filamentType ?? 'any',
      filament_color: pp.filamentColor ?? 'any',
    };
    if (isModelKey(key)) {
      model_targets.push({ machine_profile: machineProfileOf(key), ...ask });
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
