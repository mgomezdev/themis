import { describe, expect, it } from 'vitest';
import { fileKindOf, isPresliced } from './fileKind';

describe('fileKindOf', () => {
  it.each([
    ['part.gcode.3mf', 'gcode_3mf'], ['PART.GCODE.3MF', 'gcode_3mf'], ['part.gcode', 'gcode'],
    ['part.3mf', '3mf'], ['part.stl', 'stl'], ['gcode.3mf.stl', 'stl'],
  ])('%s -> %s', (name, kind) => {
    expect(fileKindOf(name)).toBe(kind);
    expect(isPresliced(name)).toBe(kind === 'gcode' || kind === 'gcode_3mf');
  });

  it('treats a missing name as a model', () => {
    expect(fileKindOf(undefined)).toBe('3mf');
    expect(isPresliced(null)).toBe(false);
  });
});
