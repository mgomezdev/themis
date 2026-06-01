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
  bedTemp: number;
  chamberTemp: number | null;
  material: Material;
  currentJobId: string | null;
  accent: string;
  note?: string;
  fanModel: number;
  fanAux: number;
  fanBox: number;
  bedTempTarget: number;
  queueOn: boolean;
  awaitingPlateClear: boolean;
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
  | 'printing' | 'queued' | 'waiting' | 'claiming' | 'slicing'
  | 'paused' | 'error' | 'offline' | 'idle' | 'ready' | 'complete'
  | 'hold' | 'in_progress' | 'partial';
