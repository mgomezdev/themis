// Queue: the display shape of a job and how a block / failure reason is worded.
import { type ApiJob } from '../../api/queue';

// ---- DisplayJob: flattened shape for rendering ----
export interface DisplayJob {
  id: string;
  rawId: number;
  fileName: string | null;
  projectId: number | null;
  projectName: string | null;
  poolName: string | null;           // BIZ-362: the print pool this plate was packed from
  plateName: string;
  status: string;
  blockReason: string | null;
  notBefore: string | null;
  materials: string[];
  eligiblePrinters: Array<{ id: number; name: string }>;
  estTime: number;
  filamentG: number;
  elapsed: number;
  progress: number;
  layer: { now: number; total: number } | null;
  sliced: boolean;
  queuePosition: number;
  ordinalRank: number;
  fileId: number;
  thumbnailPath: string | null;
  printerName: string | null;
  lowStockWarning: ApiJob['low_stock_warning'];
  sliceCache: Pick<ApiJob, 'status' | 'save_slice' | 'sliced_version_id' | 'slice_cache_info'>;
}

// ---- JobCardRich helpers ----
export function getErrorCategory(reason: string | null): string {
  if (!reason) return 'Unknown Error';
  const r = reason.toLowerCase();
  if (r.includes('slicing failed') || r.includes('slice failed')) return 'Slicing Error';
  if (r.includes('upload failed') || r.includes('upload reported failure')) return 'Upload Error';
  if (r.includes('start print failed') || r.includes('start print reported failure')) return 'Print Startup Error';
  return 'Queue Error';
}

export function getErrorMessage(reason: string | null): string {
  if (!reason) return 'An unexpected error occurred. Click for details.';
  // Strip common prefixes if present to make the card clean
  let msg = reason;
  const prefixes = [
    'slicing failed: ',
    'slicing failed:',
    'gcode upload failed: ',
    'gcode upload failed:',
    'start print failed: ',
    'start print failed:',
  ];
  for (const prefix of prefixes) {
    if (msg.toLowerCase().startsWith(prefix)) {
      msg = msg.slice(prefix.length);
      break;
    }
  }
  return msg;
}
