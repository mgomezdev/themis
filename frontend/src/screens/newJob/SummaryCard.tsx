// New job: the summary column and the submit button.
import { fmtTime } from '../../data/helpers';
import { FileInfo, Plate, PlateConfig } from './model';
import { SummaryDot } from './widgets';

export function SummaryCard({ file, plates, plateConfigs, selectedPlateIds, activePlateId, plateIsComplete }: {
  file: FileInfo | null;
  plates: Plate[];
  plateConfigs: Record<string, PlateConfig>;
  selectedPlateIds: string[];
  activePlateId: string | null;
  plateIsComplete: (id: string) => boolean;
}) {
  const totalTime = selectedPlateIds.reduce((a, id) => {
    const p = plates.find(x => x.id === id);
    return a + (p?.estTime ?? 0);
  }, 0);

  const allProjects = new Set<number>();
  selectedPlateIds.forEach(id => {
    const pid = plateConfigs[id]?.projectId;
    if (pid != null) allProjects.add(pid);
  });

  return (
    <div className="card" style={{ padding: 18 }}>
      <div className="tag-key">Job summary</div>

      <div className="row gap-2" style={{ marginTop: 12 }}>
        <SummaryDot done={!!file} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{
            color: file ? 'var(--text-1)' : 'var(--text-3)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {file ? file.name : 'No file yet'}
          </div>
        </div>
      </div>

      <div className="row gap-2" style={{ marginTop: 10, alignItems: 'flex-start' }}>
        <SummaryDot done={selectedPlateIds.length > 0} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{ color: selectedPlateIds.length ? 'var(--text-1)' : 'var(--text-3)' }}>
            {selectedPlateIds.length === 0
              ? 'No plates picked'
              : `${selectedPlateIds.length} plate${selectedPlateIds.length === 1 ? '' : 's'} → ${selectedPlateIds.length} job${selectedPlateIds.length === 1 ? '' : 's'}`}
          </div>
        </div>
      </div>

      {selectedPlateIds.length > 0 && (
        <div className="col gap-1" style={{ marginTop: 8, marginLeft: 24 }}>
          {selectedPlateIds.map(id => {
            const cfg = plateConfigs[id];
            const plate = plates.find(p => p.id === id);
            const complete = plateIsComplete(id);
            const isActive = activePlateId === id;
            return (
              <div key={id} className="row gap-2" style={{ alignItems: 'center' }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                  background: complete ? 'var(--ok)' : 'var(--warn)',
                  boxShadow: complete ? '0 0 4px var(--ok)' : 'none',
                }} />
                <span className="tiny" style={{
                  color: isActive ? 'var(--text-1)' : 'var(--text-2)',
                  fontWeight: isActive ? 500 : 400,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  flex: 1, minWidth: 0,
                }}>
                  P{plate?.index} · {cfg.jobName}
                </span>
                <span className="num tiny muted" style={{ flexShrink: 0 }}>
                  {cfg.selectedPrinters.length ?? 0}p
                </span>
              </div>
            );
          })}
        </div>
      )}

      {allProjects.size > 0 && (
        <>
          <div className="divider" />
          <div className="tag-key">Fulfills</div>
          <div className="row gap-1" style={{ marginTop: 6, flexWrap: 'wrap' }}>
            {Array.from(allProjects).map(id => (
              <span key={id} className="mono tiny" style={{ padding: '2px 8px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 999, color: 'var(--text-2)' }}>#{id}</span>
            ))}
          </div>
        </>
      )}

      <div className="divider" />

      <div className="row between">
        <span className="tag-key">Total plate time</span>
        <span className="num small">{totalTime > 0 ? fmtTime(totalTime) : '—'}</span>
      </div>
      <div className="row between" style={{ marginTop: 6 }}>
        <span className="tag-key">Slicing</span>
        <span className="small" style={{ color: 'var(--text-2)' }}>On claim</span>
      </div>
    </div>
  );
}

// ============================================================
// Main screen
// ============================================================
