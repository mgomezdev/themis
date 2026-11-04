import { useEffect, useState } from 'react';
import { getModelObjects, type ModelObject } from '../api/files';

export type ItemKind = 'stl' | '3mf_object' | '3mf_plate' | 'gcode';
export type PackRule = 'split' | 'together' | 'solo' | 'locked';

/** Which packing rules each item kind can use (BIZ-356); the first is the default. Mirrors the backend's `_RULES`. */
export const RULES: Record<ItemKind, PackRule[]> = {
  stl: ['split', 'solo'], '3mf_object': ['split', 'solo'], '3mf_plate': ['split', 'together', 'solo', 'locked'], gcode: ['locked'],
};
const RULE_LABEL: Record<PackRule, string> = {
  split: 'Split', together: 'Together', solo: 'Solo (own plate)', locked: 'Locked (as authored)',
};

/** The item a library file starts as: an STL, a gcode plate, or plate 1 of a 3MF. */
export function initialSource(fileKind: string): { kind: ItemKind; source_ref: string | null; pack_rule: PackRule } {
  if (fileKind === 'gcode' || fileKind === 'gcode_3mf') return { kind: 'gcode', source_ref: '1', pack_rule: 'locked' };
  if (fileKind === '3mf') return { kind: '3mf_plate', source_ref: '1', pack_rule: 'split' };
  return { kind: 'stl', source_ref: null, pack_rule: 'split' };
}

/**
 * What of a file a project item is (BIZ-356): for a 3MF, one of its plates (a bundle of its objects) or one object; for a
 * .gcode.3mf, which plate; and the packing rule. `fixedSource` (a saved item) keeps the plate/object and only lets the rule change.
 */
export function ItemSourcePicker({ fileId, fileKind, plateCount, kind, sourceRef, packRule, fixedSource, onChange }: {
  fileId: number;
  fileKind: string;
  plateCount: number;
  kind: ItemKind;
  sourceRef: string | null;
  packRule: PackRule;
  fixedSource?: boolean;
  onChange: (v: { kind: ItemKind; source_ref: string | null; pack_rule: PackRule }) => void;
}) {
  const [objects, setObjects] = useState<ModelObject[] | null>(null);
  const [objectsError, setObjectsError] = useState<string | null>(null);
  const is3mf = fileKind === '3mf';
  const wantObjects = is3mf && !fixedSource;

  useEffect(() => {
    if (!wantObjects) return;
    let alive = true;
    getModelObjects(fileId).then(o => { if (alive) setObjects(o); })
      .catch(e => { if (alive) setObjectsError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [fileId, wantObjects]);

  const plates = Array.from({ length: Math.max(1, plateCount) }, (_, i) => String(i + 1));
  const value = kind === '3mf_object' ? `o:${sourceRef}` : `p:${sourceRef ?? '1'}`;
  const pick = (v: string) => {
    const nextKind: ItemKind = v.startsWith('o:') ? '3mf_object' : kind === 'gcode' ? 'gcode' : '3mf_plate';
    const rule = RULES[nextKind].includes(packRule) ? packRule : RULES[nextKind][0];
    onChange({ kind: nextKind, source_ref: v.slice(2), pack_rule: rule });
  };

  return (
    <span className="row gap-1" style={{ flexWrap: 'wrap', marginTop: 4 }}>
      {(is3mf || fileKind === 'gcode_3mf') && (
        <select className="select" aria-label="Plate or object" value={value} disabled={fixedSource}
                onChange={e => pick(e.target.value)} style={{ maxWidth: 200, fontSize: 12 }}>
          {plates.map(n => <option key={n} value={`p:${n}`}>Plate {n}{is3mf ? ' (all its objects)' : ''}</option>)}
          {kind === '3mf_object' && !objects?.some(o => o.id === sourceRef) && <option value={value}>Object {sourceRef}</option>}
          {is3mf && objects?.map(o => <option key={o.id} value={`o:${o.id}`}>Object: {o.name}</option>)}
        </select>
      )}
      {kind === 'gcode' ? (
        <span className="tiny muted">As sliced</span>
      ) : (
        <select className="select" aria-label="Packing rule" value={packRule} style={{ maxWidth: 170, fontSize: 12 }}
                onChange={e => onChange({ kind, source_ref: sourceRef, pack_rule: e.target.value as PackRule })}>
          {RULES[kind].map(r => <option key={r} value={r}>{RULE_LABEL[r]}</option>)}
        </select>
      )}
      {objectsError && wantObjects && <span className="tiny muted" title={objectsError}>objects unavailable</span>}
    </span>
  );
}
