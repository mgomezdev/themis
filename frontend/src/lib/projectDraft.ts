// The Project Builder's unsaved local copy of a project ("draft"), and the pure helpers around it:
// mapping a loaded project in, fingerprinting it for dirty tracking, and wording queue errors.
import type { FilamentAsk } from './filamentAsk';
import type { Project } from '../api/projects';
import type { ItemKind, PackRule } from '../components/ItemSourcePicker';

/** An item (a model source × quantity + filament ask) as edited before saving. `serverId` is set once it exists server-side. */
export interface LocalItem extends FilamentAsk {
  localId: string;
  serverId?: number;
  file_id: number;
  file_name: string;
  quantity: number;
  sort_order: number;
  kind: ItemKind;                 // BIZ-356: STL, a 3MF plate or object, or gcode
  source_ref: string | null;
  pack_rule: PackRule;
  group_id: number | null;        // BIZ-363: its print group (null = the Default Group)
}

export interface LocalLink {
  localId: string;
  serverId?: number;
  url: string;
  label: string;
}

/** A non-printed part (hardware, e.g. magnets/screws). */
export interface LocalPart {
  localId: string;
  serverId?: number;
  name: string;
  quantity: number;
  allocated: boolean;
  unitCost: string;   // '' = no cost entered (the field is optional)
}

/** What the undo toast needs to put removed rows back where they were (items can go several at once). */
export type RemovedSnapshot =
  | { kind: 'item'; label: string; removed: { index: number; item: LocalItem }[] }
  | { kind: 'link'; label: string; index: number; link: LocalLink }
  | { kind: 'part'; label: string; index: number; part: LocalPart };

export type OrderType = 'internal' | 'customer';

/** Every field the Builder edits. Form values stay strings (`''` = empty) until saved. */
export interface ProjectDraft {
  name: string;
  customer: string;
  customerId: number | null;
  orderType: OrderType;
  onHold: boolean;
  dueDate: string;
  notes: string;
  price: string;
  items: LocalItem[];
  links: LocalLink[];
  parts: LocalPart[];
}

/** Just the four filament-ask fields of `it` (drops everything else, e.g. a LocalItem's ids). */
export const askOf = (it: FilamentAsk): FilamentAsk => ({
  filament_manufacturer: it.filament_manufacturer, filament_type: it.filament_type,
  filament_subtype: it.filament_subtype, filament_color: it.filament_color,
});

/** The API value for a part's unit cost: null when blank, so an edited part can have its cost cleared. */
export const unitCostValue = (pt: { unitCost: string }): number | null =>
  (pt.unitCost.trim() === '' ? null : Number(pt.unitCost));

let _lid = 0;
/** A React key for a row that may not exist server-side yet. Unique for the page's lifetime. */
export const newLocalId = () => String(++_lid);

/**
 * A fingerprint of everything Save would persist: equal fingerprints ⇒ nothing to save. Local and server ids are
 * left out (a save fills server ids in, and that is not an edit), but the saved items pending deletion count. The
 * customer (name + account) only counts for a customer project, since Save drops it for an internal one.
 */
export function draftSnapshot(d: ProjectDraft, deletedItemIds: readonly number[] = []): string {
  const isCustomer = d.orderType === 'customer';
  return JSON.stringify({
    name: d.name, customer: isCustomer ? d.customer : '', customerId: isCustomer ? d.customerId : null, orderType: d.orderType,
    onHold: d.onHold, dueDate: d.dueDate, notes: d.notes, price: d.price,
    items: d.items.map(i => ({
      fid: i.file_id, qty: i.quantity, rule: i.pack_rule, gid: i.group_id,
      fm: i.filament_manufacturer, ft: i.filament_type, fs: i.filament_subtype, fc: i.filament_color, so: i.sort_order,
    })),
    del: deletedItemIds,
    links: d.links.map(l => ({ url: l.url, label: l.label })),
    parts: d.parts.map(p => ({ name: p.name, qty: p.quantity, alloc: p.allocated, uc: p.unitCost })),
  });
}

export type LoadedProject = Pick<
  Project, 'name' | 'customer' | 'order_type' | 'on_hold' | 'due_date' | 'notes' | 'price' | 'customer_id' | 'customer_name' | 'items'
> & { links?: Project['links'] | null; parts?: Project['parts'] | null };

/** A loaded project as an editable draft. Items saved before BIZ-356/363 default to an STL, split, in the Default Group. */
export function projectToDraft(p: LoadedProject): ProjectDraft {
  const customerId = p.customer_id;
  return {
    name: p.name,
    // A linked account's current name wins over whatever was typed before it was linked.
    customer: customerId != null && p.customer_name ? p.customer_name : (p.customer ?? ''),
    customerId,
    // A project linked to an account is a customer project, whatever its old type said —
    // otherwise an unrelated save would unlink it.
    orderType: customerId != null ? 'customer' : ((p.order_type as OrderType) ?? 'internal'),
    onHold: p.on_hold ?? false,
    dueDate: p.due_date ?? '',
    notes: p.notes ?? '',
    price: p.price != null ? String(p.price) : '',
    items: p.items.map(it => ({
      localId: newLocalId(),
      serverId: it.id,
      file_id: it.file_id,
      file_name: it.file_name,
      quantity: it.quantity,
      kind: it.kind ?? 'stl', source_ref: it.source_ref ?? null, pack_rule: it.pack_rule ?? 'split',
      group_id: it.group_id ?? null,
      ...askOf(it),
      sort_order: it.sort_order,
    })),
    links: (p.links ?? []).map(l => ({ localId: newLocalId(), serverId: l.id, url: l.url, label: l.label ?? '' })),
    parts: (p.parts ?? []).map(pt => ({
      localId: newLocalId(),
      serverId: pt.id,
      name: pt.name,
      quantity: pt.quantity,
      allocated: pt.allocated,
      unitCost: pt.unit_cost != null ? String(pt.unit_cost) : '',
    })),
  };
}

export const GENERATE_ERRORS: Record<number, string> = {
  422: 'Add at least one part before generating.',
  502: 'Orca sidecar is offline. Check the container.',
  504: 'Generation timed out. Try fewer parts or reduce quantities.',
};

/** User-facing wording for a queue (generate) failure (`msg` is the API error, which starts with the HTTP status). */
export function parseGenerateError(msg: string): string {
  const code = parseInt(msg);
  if (!isNaN(code) && GENERATE_ERRORS[code]) return GENERATE_ERRORS[code];
  if (msg.includes('STL') || msg.includes('stl')) return 'One or more STL files are missing. Remove and re-add them.';
  return `Generation failed: ${msg}`;
}
