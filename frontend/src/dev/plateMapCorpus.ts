// The shared plate-map corpus (contracts/plate-map-cases.json), typed for the plate-map tests and the dev gallery (plate-map.html).
// backend/tests/services/test_plate_map.py runs the same cases against the Python snapshot renderer.
import corpusRaw from '../../../contracts/plate-map-cases.json?raw';
import type { Plate, Point, Rect } from '../components/plateMap/layout';

export interface ExpectObject {
  points?: Point[]; off_bed?: boolean; hit?: Rect | null; anchor?: Point; pattern?: string; fill?: string | null; on_map?: boolean;
}
export interface CorpusCase {
  name: string;
  covers: string;
  plate: Plate;
  expect: {
    view_box?: Rect;
    objects?: Record<string, ExpectObject>;
    draw_order?: string[];
    hits?: { at: Point; label: string | null }[];
    truncated?: Record<string, string>;
    list_only?: boolean;
  };
}
export interface InvalidCase { name: string; plate: unknown; message: string }

export const corpus = JSON.parse(corpusRaw) as { cases: CorpusCase[]; invalid: InvalidCase[] };
export const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
export const caseNamed = (name: string): CorpusCase => {
  const c = corpus.cases.find(x => x.name === name);
  if (!c) throw new Error(`no corpus case ${name}`);
  return clone(c);
};
