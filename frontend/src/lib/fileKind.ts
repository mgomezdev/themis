/** Library file kinds, mirroring the backend's `library_scanner.file_kind` (BIZ-190). A Bambu sliced archive
 * (`.gcode.3mf`) ends in `.3mf` but is NOT a sliceable model — check it before the plain `.3mf` case. */
export type FileKind = 'stl' | '3mf' | 'gcode' | 'gcode_3mf';

export function fileKindOf(name: string | null | undefined): FileKind {
  const lower = (name ?? '').toLowerCase();
  if (lower.endsWith('.gcode.3mf')) return 'gcode_3mf';
  if (lower.endsWith('.gcode')) return 'gcode';
  if (lower.endsWith('.stl')) return 'stl';
  return '3mf';
}

export function isPreslicedKind(kind: FileKind | null | undefined): boolean {
  return kind === 'gcode' || kind === 'gcode_3mf';
}

/** Pre-sliced (.gcode or .gcode.3mf): printed as-is, never sliced, so no print profile or overrides apply. */
export function isPresliced(name: string | null | undefined): boolean {
  return isPreslicedKind(fileKindOf(name));
}
