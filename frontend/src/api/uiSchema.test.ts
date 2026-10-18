import { describe, expect, it } from 'vitest';
import { fieldVisible, settingsKind, validateTabSchema, visibleFields } from './uiSchema';
import type { JsonSchema, JsonSchemaProperty, SchemaField } from './plugins';
import corpusRaw from '../../../contracts/ui-schema-cases.json?raw';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

type Case = { name: string; valid: boolean; doc: unknown; message?: string };
type KindCase = { prop: JsonSchemaProperty; defs?: JsonSchema['$defs']; kind: string | null };
const corpus = JSON.parse(corpusRaw) as { tab_documents: Case[]; settings_kinds: KindCase[] };

describe('validateTabSchema (the shared corpus; backend/tests/plugins/test_ui_schema.py runs the same cases)', () => {
  it.each(corpus.tab_documents.map(c => [c.name, c] as const))('%s', (_name, c) => {
    const r = validateTabSchema(clone(c.doc));
    expect(r.ok).toBe(c.valid);
    if (!c.valid) expect(!r.ok && r.message).toContain(c.message as string);
  });

  it('the corpus has accepted and rejected cases', () => {
    expect(corpus.tab_documents.filter(c => c.valid).length).toBeGreaterThan(2);
    expect(corpus.tab_documents.filter(c => !c.valid).length).toBeGreaterThan(30);
  });
});

describe('conditional visibility', () => {
  const fields = [
    { key: 'mode', label: 'Mode', type: 'select', options: [{ value: 'a', label: 'A' }] },
    { key: 'url', label: 'URL', type: 'text', visible_if: { key: 'mode', equals: 'a' } },
    { key: 'port', label: 'Port', type: 'number' },
    { key: 'on', label: 'On', type: 'checkbox' },
  ] as SchemaField[];
  it('shows a field only while its sibling holds the value', () => {
    expect(fieldVisible(fields[1], { mode: 'a' })).toBe(true);
    expect(fieldVisible(fields[1], { mode: 'b' })).toBe(false);
    expect(fieldVisible(fields[1], {})).toBe(false);
    expect(fieldVisible(fields[0], {})).toBe(true);                       // no condition: always
    expect(visibleFields(fields, { mode: 'b' }).map(f => f.key)).toEqual(['mode', 'port', 'on']);
  });
});

describe('settingsKind (the shared corpus; mirrors settings_kind in the backend)', () => {
  it.each(corpus.settings_kinds.map(c => [JSON.stringify(c.prop), c] as const))('%s', (_name, c) => {
    expect(settingsKind(c.prop, c.defs)).toBe(c.kind);
  });
});
