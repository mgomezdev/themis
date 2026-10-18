import { describe, expect, it } from 'vitest';
import { fieldVisible, settingsKind, validateTabSchema, visibleFields } from './uiSchema';
import type { SchemaField } from './plugins';

const FORM = { type: 'form', load: 'settings', save: 'settings', fields: [
  { key: 'mode', label: 'Mode', type: 'select', options: [{ value: 'a', label: 'A' }] },
  { key: 'url', label: 'URL', type: 'text', max_length: 200, visible_if: { key: 'mode', equals: 'a' } },
  { key: 'port', label: 'Port', type: 'number', min: 1, max: 65535 },
  { key: 'on', label: 'On', type: 'checkbox' }] };
const TABLE = { type: 'table', data: 'items', columns: [{ key: 'name', label: 'Name' }],
  create: { path: 'items', fields: [{ key: 'name', label: 'Name', type: 'text', required: true }] },
  row_actions: [{ label: 'Delete', method: 'DELETE', path: 'items/{id}', confirm: 'Sure?' }] };

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const withBlock = (block: unknown, edit: (b: any) => void) => { const b = clone(block); edit(b); return { blocks: [b] }; };

describe('validateTabSchema (mirrors backend/app/plugins/ui_schema.py)', () => {
  it('accepts a full document, with or without an explicit version', () => {
    expect(validateTabSchema({ schema_version: 1, title: 'T', blocks: [FORM, TABLE] }).ok).toBe(true);
    expect(validateTabSchema({ blocks: [] }).ok).toBe(true);
  });

  it.each([
    ['a newer schema version', { schema_version: 2, blocks: [] }, 'schema_version: 2 is not supported'],
    ['a string version', { schema_version: '1', blocks: [] }, 'schema_version'],
    ['a non-object', [], 'schema: must be an object'],
    ['blocks that are not a list', { blocks: {} }, 'blocks: must be a list'],
    ['an unknown top-level key', { blocks: [], extra: 1 }, 'unsupported key'],
    ['an unknown block type', { blocks: [{ type: 'chart' }] }, 'blocks[0].type: unsupported block type "chart"'],
    ['an unknown field type', withBlock(FORM, b => { b.fields[0].type = 'color'; }), 'blocks[0].fields[0].type: unsupported field type "color"'],
    ['a select without options', withBlock(FORM, b => { b.fields[0].options = []; }), 'at least one option'],
    ['visible_if naming a missing field', withBlock(FORM, b => { b.fields[1].visible_if.key = 'nope'; }), 'must name another field'],
    ['visible_if naming itself', withBlock(FORM, b => { b.fields[1].visible_if.key = 'url'; }), 'must name another field'],
    ['a constraint on the wrong field type', withBlock(FORM, b => { b.fields[3].max_length = 3; }), 'unsupported key(s) ["max_length"]'],
    ['duplicate field keys', withBlock(FORM, b => { b.fields[0].key = 'on'; }), 'field keys must be unique'],
    ['an absolute URL as a path', withBlock(FORM, b => { b.load = 'https://evil.test/x'; }), 'not a path relative'],
    ['a path that climbs out', withBlock(FORM, b => { b.save = '../../auth/keys'; }), 'not a path relative'],
    ['a rooted path', withBlock(TABLE, b => { b.row_actions[0].path = '/api/v1/printers'; }), 'blocks[0].row_actions[0].path'],
    ['a table without columns', withBlock(TABLE, b => { b.columns = []; }), 'at least one column'],
    ['an unsupported action method', withBlock(TABLE, b => { b.row_actions[0].method = 'PUT'; }), 'must be one of POST, DELETE, PATCH'],
    ['an unknown table key', withBlock(TABLE, b => { b.onclick = 'x'; }), 'unsupported key(s) ["onclick"]'],
  ])('rejects %s with a path-prefixed message', (_name, doc, message) => {
    const r = validateTabSchema(doc);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.message).toContain(message);
  });
});

describe('conditional visibility', () => {
  const fields = FORM.fields as unknown as SchemaField[];
  it('shows a field only while its sibling holds the value', () => {
    expect(fieldVisible(fields[1], { mode: 'a' })).toBe(true);
    expect(fieldVisible(fields[1], { mode: 'b' })).toBe(false);
    expect(fieldVisible(fields[1], {})).toBe(false);
    expect(fieldVisible(fields[0], {})).toBe(true);                       // no condition: always
    expect(visibleFields(fields, { mode: 'b' }).map(f => f.key)).toEqual(['mode', 'port', 'on']);
  });
});

describe('settingsKind (mirrors settings_kind in the backend)', () => {
  it.each([
    [{ type: 'boolean' }, 'boolean'],
    [{ type: 'integer', minimum: 1 }, 'number'],
    [{ anyOf: [{ type: 'number' }, { type: 'null' }] }, 'number'],
    [{ type: 'string' }, 'text'],
    [{ anyOf: [{ type: 'string' }, { type: 'null' }] }, 'text'],
    [{ type: 'array', items: { type: 'string' } }, 'list'],
    [{ type: 'array', items: { type: 'string', enum: ['a', 'b'] } }, 'choices'],
    [{ type: 'object' }, null],
    [{ type: 'array', items: { type: 'object' } }, null],
    [{ type: 'array', items: { type: 'integer' } }, null],
    [{}, null],
    [{ anyOf: [{ type: 'string' }, { type: 'integer' }] }, null],
  ])('%j -> %s', (prop, kind) => {
    expect(settingsKind(prop as never)).toBe(kind);
  });

  it('resolves an enum through $defs', () => {
    const defs = { Level: { type: 'string', enum: ['low', 'high'] } };
    expect(settingsKind({ $ref: '#/$defs/Level' }, defs)).toBe('text');
    expect(settingsKind({ allOf: [{ $ref: '#/$defs/Level' }], default: 'low' }, defs)).toBe('text');
    expect(settingsKind({ type: 'array', items: { $ref: '#/$defs/Level' } }, defs)).toBe('choices');
  });
});
