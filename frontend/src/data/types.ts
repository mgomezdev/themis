export interface Material {
  name: string;
  type: string;
  color: string;
}

export interface Printer {
  id: string;
  name: string;
  nickname: string;
  model: string;
  /** The plugin serving this printer (its connection form comes from the plugin's declared models). */
  pluginId?: string;
  /** Set when the printer's plugin is disabled or removed: the printer takes no work until it is back. */
  dormantReason?: string;
  badge: string;
  buildVolume: string;
  capabilities: string[];
  chamber: boolean;
  status: 'printing' | 'idle' | 'paused' | 'error' | 'offline' | 'claiming';
  progress: number;
  timeRemaining: number;
  timeElapsed: number;
  layer: { now: number; total: number } | null;
  nozzleTemp: number;
  nozzleTempTarget?: number;
  /** Per-tool temperatures for tool-changer printers (Snapmaker U1: T0..T3); absent for single-nozzle printers. */
  nozzles?: { index: number; temp: number; target: number }[];
  bedTemp: number;
  chamberTemp: number | null;
  material: Material;
  materials?: Material[];
  currentJobId: string | null;
  accent: string;
  note?: string;
  fanModel: number;
  fanAux: number;
  fanBox: number;
  bedTempTarget: number;
  queueOn: boolean;
  awaitingPlateClear: boolean;
  noSnapshotsWhileIdle: boolean;
  alarmCount?: number;
  alarmSeverity?: 'info' | 'warning' | 'error' | 'fatal' | null;
}

export interface OrderPart {
  id: string;
  name: string;
  qty: number;
  printed: number;
  material: string;
  est: number;
  thumbColor: string;
}

export interface Order {
  id: string;
  type: 'customer' | 'internal';
  customer: string;
  title: string;
  placed: string;
  due: string;
  status: 'queued' | 'in_progress' | 'partial' | 'complete' | 'hold';
  notes: string;
  parts: OrderPart[];
}

export interface JobPart {
  orderId: string;
  partId: string;
  qty: number;
}

export interface Job {
  id: string;
  plateName: string;
  status: 'printing' | 'queued' | 'complete' | 'paused' | 'error';
  printerId: string | null;
  eligiblePrinters: string[];
  actualPrinter?: string;
  material: string;
  parts: JobPart[];
  estTime: number;
  elapsed: number;
  progress: number;
  layer?: { now: number; total: number };
  priority: number;
  sliced: boolean;
  note?: string;
  completedAt?: string;
}

export interface FilamentProfile {
  printerId: string;
  name: string;
  nozzle: string;
  bedTemp: number;
  hotendTemp: number;
  layerHeight: number;
  notes: string;
}

export interface PurchaseLink {
  vendor: string;
  url: string;
}

export interface Filament {
  id: string;
  name: string;
  manufacturer: string;
  type: string;
  subtype: string;
  color: string;
  colorName: string;
  diameter: number;
  dryTemp: number;
  purchaseLinks: PurchaseLink[];
  profiles: FilamentProfile[];
  notes: string;
  favorite?: boolean;
}

export interface ProcessPreset {
  id: string;
  printerId: string;
  name: string;
  nozzle: string;
  layerHeight: number;
  infill: number;
  walls: number;
  speed: string;
  description: string;
}

export interface Tag {
  id: string;
  name: string;
  color: string;
  category: string;
}

export interface FileEntry {
  id: string;
  name: string;
  size: string;
  parts: number;
  updated: string;
  thumbColor: string;
  folder: string;
  tags: string[];
}

export type StatusKey =
  | 'printing' | 'queued' | 'waiting' | 'claiming' | 'slicing' | 'uploading'
  | 'paused' | 'error' | 'offline' | 'idle' | 'ready' | 'complete'
  | 'hold' | 'in_progress' | 'partial' | 'blocked' | 'failed' | 'cancelled';

export interface LibraryFile {
  id: number;
  original_filename: string;
  relative_path: string;
  folder: string;
  size_bytes: number;
  plate_count: number;
  uploaded_at: string;
  missing: boolean;
  tags: { id: number; name: string; color: string; category: string }[];
  thumbnail_url: string | null;
  plate_thumbnails: { plate_number: number; thumbnail_url: string }[];
  /** 3mf / stl = sliceable model; gcode / gcode_3mf = pre-sliced (BIZ-190). */
  kind: 'stl' | '3mf' | 'gcode' | 'gcode_3mf';
  /** Slicing cache: how many cached versions this model has (BIZ-196). */
  sliced_version_count: number;
  /** Slicing cache: set when this file IS a cached version of a model. */
  sliced_version: SlicedVersionSummary | null;
  /** Pre-sliced G-code only (BIZ-263): `known` false = legacy/unknown machine eligibility; `model_uuids` = printer models it may be
   *  sent to (registry ids). Null for model files, which the slicer targets at the printer. */
  eligibility: { known: boolean; model_uuids: string[] } | null;
}

export interface SlicedVersionSummary {
  id: number;
  source_file_id: number | null;
  source_filename: string | null;
  plate_number: number;
  machine_preset: string;
  process_preset: string;
  filament_presets: string[];
  filament_type: string;
  filament_color: string;
}

export interface FolderNode {
  name: string;
  path: string;
  count: number;
  children: Record<string, FolderNode>;
}
