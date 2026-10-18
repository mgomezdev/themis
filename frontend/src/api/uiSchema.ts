/** Runtime validation of the declarative plugin UI contract (BIZ-303). The backend validates the same constructs when a plugin
 *  registers (`backend/app/plugins/ui_schema.py`: keep the two in step); this re-checks what actually arrives, so
 *  a document written for a newer contract version fails closed for that one tab or field instead of rendering something this build
 *  does not understand. The cases in `contracts/ui-schema-cases.json` run against both validators, so they cannot drift. */
import type { JsonSchema, JsonSchemaProperty, SchemaBlock, SchemaField, TabSchema } from './plugins';

export const SCHEMA_VERSION = 1;
export const FIELD_TYPES = ['text', 'number', 'textarea', 'checkbox', 'select'] as const;
export const BLOCK_TYPES = ['form', 'table'] as const;
const ACTION_METHODS = ['POST', 'DELETE', 'PATCH'];

export type SchemaResult = { ok: true; schema: TabSchema } | { ok: false; message: string };

class Problem extends Error {}
const need = (cond: unknown, where: string, message: string): void => { if (!cond) throw new Problem(`${where}: ${message}`); };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '';
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

const isScalar = (v: unknown): boolean => typeof v === 'string' || typeof v === 'boolean' || isNum(v);
/** `v` may be absent (undefined or null) or must be text. */
const optText = (v: unknown, where: string) => need(v === undefined || v === null || typeof v === 'string', where, 'must be text');
const exactKeys = (o: Obj, keys: string[]) => Object.keys(o).length === keys.length && keys.every(k => k in o);

function noUnknown(o: Obj, allowed: string[], where: string) {
  const extra = Object.keys(o).filter(k => !allowed.includes(k));
  need(extra.length === 0, where, `unsupported key(s) ${JSON.stringify(extra)}`);
}

function checkPath(v: unknown, where: string) {
  need(typeof v === 'string' && v !== '', where, 'a path is required');
  const p = v as string;
  need(/^[A-Za-z0-9_\-./{}]+$/.test(p) && !p.split('/').includes('..') && !p.startsWith('/') && !p.includes(':'),
    where, `${JSON.stringify(p)} is not a path relative to the plugin's API`);
}

function checkField(f: unknown, where: string, siblings: string[]) {
  need(isObj(f), where, 'a field must be an object');
  const o = f as Obj;
  need((FIELD_TYPES as readonly unknown[]).includes(o.type), `${where}.type`, `unsupported field type ${JSON.stringify(o.type)}`);
  const extra = o.type === 'select' ? ['options'] : o.type === 'number' ? ['min', 'max'] : o.type === 'checkbox' ? [] : ['max_length'];
  noUnknown(o, ['key', 'label', 'type', 'required', 'help', 'visible_if', ...extra], where);
  need(typeof o.key === 'string' && /^[A-Za-z_][A-Za-z0-9_]*$/.test(o.key), `${where}.key`, 'a field key must be an identifier');
  need(isStr(o.label), `${where}.label`, 'a label is required');
  need(o.required === undefined || typeof o.required === 'boolean', `${where}.required`, 'must be true or false');
  optText(o.help, `${where}.help`);
  if (o.type === 'select') {
    need(Array.isArray(o.options) && o.options.length > 0, `${where}.options`, 'a select needs at least one option');
    (o.options as unknown[]).forEach((opt, i) =>
      need(isObj(opt) && exactKeys(opt, ['value', 'label']) && typeof opt.value === 'string' && isStr(opt.label), `${where}.options[${i}]`, 'an option is {value, label}'));
  }
  if (o.type === 'number') {
    need(o.min == null || isNum(o.min), `${where}.min`, 'must be a number');
    need(o.max == null || isNum(o.max), `${where}.max`, 'must be a number');
    need(!isNum(o.min) || !isNum(o.max) || o.min <= o.max, where, 'min is greater than max');
  }
  if (o.max_length != null) need(Number.isInteger(o.max_length) && (o.max_length as number) > 0, `${where}.max_length`, 'must be a positive integer');
  if (o.visible_if !== undefined) {
    const v = o.visible_if;
    need(isObj(v) && exactKeys(v, ['key', 'equals']), `${where}.visible_if`, 'is {key, equals}');
    need(typeof (v as Obj).key === 'string' && siblings.includes((v as Obj).key as string) && (v as Obj).key !== o.key, `${where}.visible_if.key`, 'must name another field of this form');
    need(isScalar((v as Obj).equals), `${where}.visible_if.equals`, 'must be text, a number or true/false');
  }
}

function checkFields(fields: unknown, where: string) {
  need(Array.isArray(fields) && fields.length > 0, where, 'at least one field is required');
  const keys = (fields as unknown[]).map(f => (isObj(f) ? f.key : undefined)).filter((k): k is string => typeof k === 'string');
  need(new Set(keys).size === keys.length, where, 'field keys must be unique');
  (fields as unknown[]).forEach((f, i) => checkField(f, `${where}[${i}]`, keys));
}

function checkBlock(b: unknown, where: string) {
  need(isObj(b), where, 'a block must be an object');
  const o = b as Obj;
  need((BLOCK_TYPES as readonly unknown[]).includes(o.type), `${where}.type`, `unsupported block type ${JSON.stringify(o.type)}`);
  optText(o.title, `${where}.title`);
  optText(o.description, `${where}.description`);
  if (o.type === 'form') {
    noUnknown(o, ['type', 'title', 'description', 'load', 'save', 'fields'], where);
    checkPath(o.load, `${where}.load`);
    checkPath(o.save, `${where}.save`);
    checkFields(o.fields, `${where}.fields`);
    return;
  }
  noUnknown(o, ['type', 'title', 'description', 'data', 'columns', 'create', 'row_actions', 'empty'], where);
  checkPath(o.data, `${where}.data`);
  need(Array.isArray(o.columns) && o.columns.length > 0, `${where}.columns`, 'at least one column is required');
  (o.columns as unknown[]).forEach((c, i) => need(isObj(c) && exactKeys(c, ['key', 'label']) && typeof c.key === 'string' && isStr(c.label), `${where}.columns[${i}]`, 'a column is {key, label}'));
  optText(o.empty, `${where}.empty`);
  if (o.create !== undefined && o.create !== null) {
    need(isObj(o.create), `${where}.create`, 'must be an object');
    const c = o.create as Obj;
    noUnknown(c, ['path', 'label', 'fields'], `${where}.create`);
    checkPath(c.path, `${where}.create.path`);
    checkFields(c.fields, `${where}.create.fields`);
  }
  if (o.row_actions !== undefined && o.row_actions !== null) {
    need(Array.isArray(o.row_actions), `${where}.row_actions`, 'must be a list');
    (o.row_actions as unknown[]).forEach((a, i) => {
      const w = `${where}.row_actions[${i}]`;
      need(isObj(a), w, 'an action must be an object');
      const ao = a as Obj;
      noUnknown(ao, ['label', 'method', 'path', 'confirm', 'body'], w);
      need(isStr(ao.label), `${w}.label`, 'a label is required');
      need(ACTION_METHODS.includes(ao.method as string), `${w}.method`, `must be one of ${ACTION_METHODS.join(', ')}`);
      checkPath(ao.path, `${w}.path`);
      optText(ao.confirm, `${w}.confirm`);
      need(ao.body === undefined || ao.body === null || isObj(ao.body), `${w}.body`, 'must be an object');
    });
  }
}

/** Accept `raw` only when it is a valid v1 tab document; otherwise say why (the message is shown to the admin). */
export function validateTabSchema(raw: unknown): SchemaResult {
  try {
    need(isObj(raw), 'schema', 'must be an object');
    const o = raw as Obj;
    const version = o.schema_version ?? SCHEMA_VERSION;
    need(version === SCHEMA_VERSION, 'schema_version', `${JSON.stringify(version)} is not supported (this Themis renders schema_version ${SCHEMA_VERSION})`);
    noUnknown(o, ['schema_version', 'title', 'blocks'], 'schema');
    optText(o.title, 'title');
    need(Array.isArray(o.blocks), 'blocks', 'must be a list');
    (o.blocks as unknown[]).forEach((b, i) => checkBlock(b, `blocks[${i}]`));
    return { ok: true, schema: raw as TabSchema };
  } catch (e) {
    if (e instanceof Problem) return { ok: false, message: e.message };
    throw e;
  }
}

/** Conditional visibility: a field with `visible_if` shows only while the named sibling holds the expected value. */
export function fieldVisible(f: SchemaField, values: Record<string, unknown>): boolean {
  if (!f.visible_if) return true;
  const have = values[f.visible_if.key];
  return have === f.visible_if.equals || String(have ?? '') === String(f.visible_if.equals);
}

export function visibleFields(fields: SchemaField[], values: Record<string, unknown>): SchemaField[] {
  return fields.filter(f => fieldVisible(f, values));
}

export type { SchemaBlock };

// ---- the settings form ----

export type SettingsKind = 'boolean' | 'number' | 'text' | 'list' | 'choices';

function resolve(p: JsonSchemaProperty, defs: JsonSchema['$defs']): JsonSchemaProperty {
  const ref = p.$ref ?? p.allOf?.find(a => a.$ref)?.$ref;
  if (!ref) return p;
  const { $ref: _r, allOf: _a, ...rest } = p;
  return { ...(defs?.[ref.split('/').pop() ?? ''] ?? {}), ...rest };
}

/** The input a settings property renders as, or null when the contract does not cover it (mirrors `settings_kind` in the backend). */
export function settingsKind(prop: JsonSchemaProperty, defs?: JsonSchema['$defs']): SettingsKind | null {
  const p = resolve(prop, defs);
  const branches = (p.anyOf ? p.anyOf.map(b => resolve(b, defs)) : [p]).filter(b => b.type !== 'null');
  const kinds = new Set<SettingsKind>();
  for (const b of branches) {
    const t = Array.isArray(b.type) ? b.type.find(x => x !== 'null') : b.type;
    if (t === 'boolean') kinds.add('boolean');
    else if (t === 'integer' || t === 'number') kinds.add('number');
    else if (t === 'string' || (t === undefined && b.enum?.every(e => typeof e === 'string') && b.enum.length > 0)) kinds.add('text');
    else if (t === 'array') {
      const items = resolve(b.items ?? {}, defs);
      if ((items.type !== undefined && items.type !== 'string') || (items.type === undefined && !items.enum?.length)) return null;
      kinds.add(items.enum?.length ? 'choices' : 'list');
    } else return null;
  }
  return kinds.size === 1 ? [...kinds][0] : null;
}
