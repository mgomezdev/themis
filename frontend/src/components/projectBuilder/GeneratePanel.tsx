// The Project builder's job generation UI: choosing printers + process preset, then the result or error banner.
import { Icons } from '../icons';
import { PrinterEligibilityPicker } from '../PrinterEligibilityPicker';
import { ProcessPresetPicker } from '../ProcessPresetPicker';

/** Printer + preset choice. The selection is kept by the caller so a Retry reuses it. */
export function GeneratePicker({ eligiblePrinterIds, setEligiblePrinterIds, processPreset, setProcessPreset, disabled, onCancel, onGenerate }: {
  eligiblePrinterIds: number[];
  setEligiblePrinterIds: (ids: number[]) => void;
  processPreset: string | null;
  setProcessPreset: (preset: string | null) => void;
  disabled: boolean;
  onCancel: () => void;
  onGenerate: (printerIds: number[]) => void;
}) {
  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8,
      padding: '14px 16px', background: 'var(--bg-2)',
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-3)',
                    textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
        Eligible printers
      </div>
      <PrinterEligibilityPicker selected={eligiblePrinterIds} onChange={setEligiblePrinterIds} />
      <div style={{ marginTop: 12 }}>
        <ProcessPresetPicker printerIds={eligiblePrinterIds} value={processPreset} onChange={setProcessPreset} />
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
        <button
          className={eligiblePrinterIds.length === 0 ? 'btn sm' : 'btn primary sm'}
          onClick={() => onGenerate(eligiblePrinterIds)}
          disabled={disabled}
          title={eligiblePrinterIds.length === 0
            ? 'No printers selected — jobs will be created but not dispatched'
            : undefined}
        >
          {eligiblePrinterIds.length === 0 ? 'Create without dispatch' : 'Create'}
        </button>
      </div>
    </div>
  );
}

export function GenerateSuccess({ jobCount, onOpenQueue, onOpenProject }: {
  jobCount: number;
  onOpenQueue: () => void;
  /** Omitted when there is no saved project to open. */
  onOpenProject?: () => void;
}) {
  return (
    <div style={{
      border: '1px solid var(--ok)', borderRadius: 8,
      padding: '12px 16px', background: 'oklch(55% 0.15 142 / 0.06)',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ color: 'var(--ok)', flexShrink: 0 }}>{Icons.check}</span>
      <span style={{ fontSize: 14, flex: 1 }}>
        {jobCount} job{jobCount !== 1 ? 's' : ''} added to queue
      </span>
      <button className="btn sm" onClick={onOpenQueue}>Queue</button>
      {onOpenProject && (
        <button className="btn sm" onClick={onOpenProject}>
          Details
        </button>
      )}
    </div>
  );
}

export function GenerateError({ message, busy, onRetry }: { message: string; busy: boolean; onRetry: () => void }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
      borderRadius: 6, padding: '10px 14px',
    }}>
      <span style={{ color: 'var(--err)', flexShrink: 0 }}>{Icons.alert}</span>
      <span style={{ fontSize: 13, color: 'var(--err)', flex: 1 }}>{message}</span>
      <button className="btn sm" onClick={onRetry} disabled={busy}>Retry</button>
    </div>
  );
}
