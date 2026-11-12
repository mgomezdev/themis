// New job: the local plate / config / file shapes and the helpers that build them.
import { useState, useEffect } from 'react';
import type { ApiPrinter } from '../../api/printers';
import { type ApiPlate } from '../../api/queue';
import { apiFetch } from '../../api/client';
import { type PerPrinterCfg } from '../../components/PerPrinterConfig';
import { type FileKind } from '../../lib/fileKind';

export interface Plate {
  id: string;
  index: number;
  name: string;
  estTime: number;
  thumbColor: string;
  thumbnailPath: string | null;
  fileId: number | null;
}

export interface PlateConfig {
  selected: boolean;
  jobName: string;
  projectId: number | null;
  selectedPrinters: string[];
  perPrinter: Record<string, PerPrinterCfg>;
  confirmedOverrides: Record<string, string>;
}

export interface FileInfo {
  name: string;
  size: number;
  type: FileKind;
  folder?: string;
}

// ============================================================
// Misc helpers
// ============================================================

// ============================================================
// Printer list hook
// ============================================================

export function usePrinterList(): ApiPrinter[] {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  useEffect(() => {
    apiFetch('/api/v1/printers')
      .then(r => r.json())
      .then(setPrinters)
      .catch(console.error);
  }, []);
  return printers;
}

// ============================================================
// Small sub-components
// ============================================================

export function platesToLocal(apiPlates: ApiPlate[], fileId: number): Plate[] {
  return apiPlates.map(p => ({
    id: `plate-${p.plate_number}`,
    index: p.plate_number,
    name: `Plate ${p.plate_number}`,
    estTime: Math.round(p.estimated_time / 60),
    thumbColor: '#2a3552',
    thumbnailPath: p.thumbnail_path,
    fileId,
  }));
}

export function defaultConfigForPlate(plate: Plate, projectId: number | null = null): PlateConfig {
  return {
    selected: true,
    jobName: plate.name,
    projectId,
    selectedPrinters: [],
    perPrinter: {},
    confirmedOverrides: {},
  };
}
