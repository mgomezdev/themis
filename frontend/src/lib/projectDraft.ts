// The Project builder's unsaved local copy of a project ("draft"), and the pure helpers around it:
// mapping a loaded project in, fingerprinting it for dirty tracking, and wording generate errors.
import type { FilamentAsk } from './filamentAsk';
import type { Project } from '../api/projects';

/** A part item (one STL + quantity + filament ask) as edited before saving. `serverId` is set once it exists server-side. */
export interface LocalItem extends FilamentAsk {
  localId: string;
  serverId?: number;
  file_id: number;
  file_name: string;
  quantity: number;
  sort_order: number;
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

/** What the undo toast needs to put a removed row back where it was. */
export type RemovedSnapshot =
  | { kind: 'item'; label: string; index: number; item: LocalItem }
  | { kind: 'link'; label: string; index: number; link: LocalLink }
  | { kind: 'part'; label: string; index: number; part: LocalPart };

export type OrderType = 'internal' | 'customer';

/** Every field the builder edits. Form values stay strings (`''` = empty) until saved. */
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
 * A fingerprint of everything Save would persist: equal fingerprints ⇒ nothing to save. Local ids are left out,
 * and the customer (name + account) only counts for a customer project, since Save drops it for an internal one.
 */
export function draftSnapshot(d: ProjectDraft): string {
  const isCustomer = d.orderType === 'customer';
  return JSON.stringify({
    name: d.name, customer: isCustomer ? d.customer : '', customerId: isCustomer ? d.customerId : null, orderType: d.orderType,
    onHold: d.onHold, dueDate: d.dueDate, notes: d.notes, price: d.price,
    items: d.items.map(i => ({
      fid: i.file_id, qty: i.quantity,
      fm: i.filament_manufacturer, ft: i.filament_type, fs: i.filament_subtype, fc: i.filament_color, so: i.sort_order,
    })),
    links: d.links.map(l => ({ url: l.url, label: l.label, sid: l.serverId })),
    parts: d.parts.map(p => ({ name: p.name, qty: p.quantity, alloc: p.allocated, uc: p.unitCost, sid: p.serverId })),
  });
}

export type LoadedProject = Pick<
  Project, 'name' | 'customer' | 'order_type' | 'on_hold' | 'due_date' | 'notes' | 'price' | 'customer_id' | 'customer_name' | 'items'
> & { links?: Project['links'] | null; parts?: Project['parts'] | null };

/** A loaded project as an editable draft. */
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

/** User-facing wording for a generate failure (`msg` is the API error, which starts with the HTTP status). */
export function parseGenerateError(msg: string): string {
  const code = parseInt(msg);
  if (!isNaN(code) && GENERATE_ERRORS[code]) return GENERATE_ERRORS[code];
  if (msg.includes('STL') || msg.includes('stl')) return 'One or more STL files are missing. Remove and re-add them.';
  return `Generation failed: ${msg}`;
}
