/** The filament tuple (manufacturer, type, subtype, color) and the one matching rule — the client-side mirror of the server's
 *  `services/filament_tuple.py` (spec docs/superpowers/specs/2026-10-19-filament-matching-design.md). Used for the "matches T1" preview
 *  and to turn an inventory material into an ask; the server decides eligibility. `null` = undefined (matches anything). */
import type { InvMaterial } from '../api/inventory';
import type { LoadedFilament } from '../api/printers';

export interface FilamentAsk {
  filament_manufacturer: string | null;
  filament_type: string | null;
  filament_subtype: string | null;   // "any" is the wildcard (a real value, never "undefined"); plain filament is "standard"
  filament_color: string | null;
}

export const NO_ASK: FilamentAsk = { filament_manufacturer: null, filament_type: null, filament_subtype: null, filament_color: null };
export const ANY = 'any';
export const STANDARD = 'standard';
export const SUBTYPES = [ANY, STANDARD, 'plus', 'silk', 'cf', 'gf', 'hf'];
export const FILAMENT_TYPES = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PA', 'PC', 'HIPS'];

const clean = (v: string | null | undefined): string | null => {
  const t = (v ?? '').trim();
  return t ? t : null;
};
const name = (v: string | null | undefined): string | null => clean(v)?.toLowerCase() ?? null;
export const normColor = (v: string | null | undefined): string | null => clean(clean(v)?.replace(/^#+/, ''))?.toLowerCase() ?? null;

const SUFFIX: Record<string, string> = { '+': 'plus', plus: 'plus', silk: 'silk', cf: 'cf', gf: 'gf', hf: 'hf', basic: STANDARD, standard: STANDARD };

/** "PLA+" -> PLA/plus, "PLA-CF" -> PLA/cf, "PLA Silk" -> PLA/silk, plain -> standard (same suffix rules as the server). */
export function splitType(raw: string | null | undefined): { type: string | null; subtype: string | null } {
  const text = clean(raw);
  if (!text) return { type: null, subtype: null };
  let base: string, suffix: string;
  if (text.endsWith('+')) { base = text.slice(0, -1).trim(); suffix = '+'; }
  else { const m = text.split(/[\s_/-]+/); base = m[0]; suffix = m.slice(1).join(' ').trim(); }
  if (!base) return { type: text, subtype: STANDARD };
  if (!suffix) return { type: base, subtype: STANDARD };
  return { type: base, subtype: SUFFIX[suffix.toLowerCase()] ?? suffix.toLowerCase() };
}

/** An ask from an inventory material: picking one only fills the four parts, nothing links the job to it afterwards. */
export function askFromMaterial(m: Pick<InvMaterial, 'vendor' | 'material' | 'subtype' | 'color_hex'>): FilamentAsk {
  const type = clean(m.material);
  return {
    filament_manufacturer: clean(m.vendor),
    filament_type: type,
    filament_subtype: type ? (clean(m.subtype) ?? STANDARD) : null,
    filament_color: m.color_hex ? `#${m.color_hex.replace(/^#/, '')}` : null,
  };
}

/** A usable ask: a type always has a subtype (the server refuses otherwise). */
export function normalizeAsk(a: FilamentAsk): FilamentAsk {
  const type = clean(a.filament_type);
  return {
    filament_manufacturer: clean(a.filament_manufacturer),
    filament_type: type,
    filament_subtype: type ? (clean(a.filament_subtype) ?? ANY) : clean(a.filament_subtype),
    filament_color: clean(a.filament_color),
  };
}

export const askIsUndefined = (a: FilamentAsk): boolean => !(a.filament_manufacturer || a.filament_type || a.filament_subtype || a.filament_color);

export function describeAsk(a: FilamentAsk): string {
  const color = a.filament_color ? `#${normColor(a.filament_color)?.toUpperCase()}` : null;
  return [a.filament_manufacturer, a.filament_type, a.filament_subtype, color].filter(Boolean).join(' ') || 'any filament';
}

type SlotLike = Pick<LoadedFilament, 'type' | 'color'> & { subtype?: string | null; manufacturer?: string | null; empty?: boolean };

/** A tool-changer placeholder for an empty tool is not loaded; a hand-entered slot without a type still is. */
export const slotIsLoaded = (s: SlotLike): boolean => !!clean(s.type) || !s.empty;

function slotTuple(s: SlotLike): { manufacturer: string | null; type: string | null; subtype: string | null; color: string | null } {
  let type = clean(s.type);
  let subtype = clean(s.subtype);
  if (type && !subtype) ({ type, subtype } = splitType(type));
  return { manufacturer: clean(s.manufacturer), type, subtype, color: clean(s.color) };
}

/** Does `slot` satisfy `ask`? Every defined part must match; an unknown slot manufacturer fails a manufacturer ask. */
export function slotSatisfies(ask: FilamentAsk, slot: SlotLike): boolean {
  const t = slotTuple(slot);
  if (ask.filament_manufacturer && name(ask.filament_manufacturer) !== name(t.manufacturer)) return false;
  if (ask.filament_type && name(ask.filament_type) !== name(t.type)) return false;
  if (ask.filament_subtype && name(ask.filament_subtype) !== ANY && name(ask.filament_subtype) !== name(t.subtype)) return false;
  if (ask.filament_color && normColor(ask.filament_color) !== normColor(t.color)) return false;
  return true;
}

/** The lowest loaded slot index that satisfies the ask and is not in conflict, or null. */
export function firstMatchingSlot(ask: FilamentAsk, slots: SlotLike[], conflicts: number[] = []): number | null {
  const i = slots.findIndex((s, idx) => slotIsLoaded(s) && !conflicts.includes(idx) && slotSatisfies(ask, s));
  return i >= 0 ? i : null;
}
