// The Builder's bulk filament editor: one filament ask for every selected item (Items tab).
import { FilamentAskEditor } from '../FilamentAskEditor';
import type { FilamentAsk } from '../../lib/filamentAsk';
import type { InvMaterial } from '../../api/inventory';

export interface BulkAsk { ids: string[]; value: FilamentAsk }

export function BulkFilamentCard({ bulkAsk, materials, catalogEnabled, onChange, onCancel, onApply }: {
  bulkAsk: BulkAsk;
  materials: InvMaterial[];
  catalogEnabled: boolean;
  onChange: (next: BulkAsk) => void;
  onCancel: () => void;
  onApply: () => void;
}) {
  return (
    <div className="card" style={{ padding: 12 }} data-testid="bulk-filament">
      <div className="label">Filament for {bulkAsk.ids.length} selected</div>
      <FilamentAskEditor id="bulk-ask" value={bulkAsk.value} onChange={v => onChange({ ...bulkAsk, value: v })}
                         materials={materials} catalogEnabled={catalogEnabled} />
      <div className="row gap-2" style={{ marginTop: 8, justifyContent: 'flex-end' }}>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
        <button className="btn primary sm" onClick={onApply}>Apply</button>
      </div>
    </div>
  );
}
