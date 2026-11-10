// New job, step 2: one plate's printers, profile and filament asks.
import React from 'react';
import { fmtTime } from '../../data/helpers';
import type { ApiPrinter } from '../../api/printers';
import { plateThumbnailUrl, type EmbeddedSetting, type ModelFilament } from '../../api/queue';
import { PerPrinterConfig, defaultPerPrinterCfg, type PerPrinterCfg } from '../../components/PerPrinterConfig';
import { isModelKey, modelConfigSource } from '../../components/ModelTargets';
import { PrinterTargetPicker } from '../../components/PrinterTargetPicker';
import { OverridePanel } from '../../components/OverridePanel';
import { GcodeWarning } from '../../components/GcodeWarning';
import { Plate, PlateConfig } from './model';
import { ProjectPicker, QueueToggle, StepNum } from './widgets';

function PlateThumbnail({
  fileId,
  thumbnailPath,
  label,
  style,
}: {
  fileId: number | null;
  thumbnailPath: string | null;
  label: string;
  style?: React.CSSProperties;
}) {
  const url = fileId != null ? plateThumbnailUrl(fileId, thumbnailPath) : null;
  return (
    <div style={{
      borderRadius: 8, flexShrink: 0,
      background: 'var(--thumb-fallback)',
      border: '1px solid var(--border-2)',
      display: 'grid', placeItems: 'center',
      overflow: 'hidden',
      ...style,
    }}>
      {url ? (
        <img src={url} alt={label}
             style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : (
        <span style={{ color: 'rgba(255,255,255,0.7)', fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
          {label}
        </span>
      )}
    </div>
  );
}

// ============================================================
// PlateConfigPanel
// ============================================================

export function PlateConfigPanel({ plate, config, isMultiPlate, gcode, printers, modelFilaments, embeddedSettings, onSetField, onTogglePrinter, onSetPerPrinter, onSetProject, onToggleQueued, onSetOverrides }: {
  plate: Plate;
  config: PlateConfig;
  isMultiPlate: boolean;
  gcode: boolean;
  printers: ApiPrinter[];
  modelFilaments: ModelFilament[];
  embeddedSettings: EmbeddedSetting[];
  onSetField: (patch: Partial<PlateConfig>) => void;
  onTogglePrinter: (id: string) => void;
  onSetPerPrinter: (printerId: string, patch: Partial<PerPrinterCfg>) => void;
  onSetProject: (id: number | null) => void;
  onToggleQueued: (v: boolean) => void;
  onSetOverrides: (v: Record<string, string>) => void;
}) {
  const queued = !!config.selected;

  return (
    <div className="col gap-4">
      {/* Plate banner with queue toggle */}
      <div className="row gap-3">
        <PlateThumbnail
          fileId={plate.fileId}
          thumbnailPath={plate.thumbnailPath}
          label={`P${plate.index}`}
          style={{ width: 56, height: 50, opacity: queued ? 1 : 0.55 }}
        />
        <div className="col grow min-0">
          <div style={{ fontSize: 14, fontWeight: 500, color: queued ? 'var(--text-1)' : 'var(--text-3)' }}>
            {plate.name}
          </div>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            est. {fmtTime(plate.estTime)}
          </div>
        </div>
        {isMultiPlate && <QueueToggle checked={queued} onChange={onToggleQueued} />}
      </div>

      {/* Body: dims when plate is skipped */}
      <div
        style={{
          opacity: queued ? 1 : 0.5,
          pointerEvents: queued ? 'auto' : 'none',
          transition: 'opacity 120ms',
        }}
        aria-disabled={!queued}>
        <div className="col gap-4">
          {/* Eligible printers */}
          <div>
            <div className="row between" style={{ marginBottom: 10, alignItems: 'baseline' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
                  <StepNum n={2} done={config.selectedPrinters.length > 0} />
                  Eligible printers
                </div>
                <div className="tiny muted" style={{ marginTop: 2, marginLeft: 30 }}>
                  {gcode
                    ? 'Choose the printers (or printer models) this gcode was sliced for. Set the filament ask for each.'
                    : 'Choose which printers may claim this plate. Configure preset + filament for each.'}
                </div>
              </div>
            </div>
            {gcode && <div style={{ marginBottom: 10 }}><GcodeWarning /></div>}
            <PrinterTargetPicker printers={printers} selected={config.selectedPrinters} onToggle={onTogglePrinter} />
            {config.selectedPrinters.length > 0 && (
              <div className="col gap-3" style={{ marginTop: 14 }}>
                {config.selectedPrinters.map(pid => {
                  // A make/model target is configured through a representative printer of that model.
                  const src = isModelKey(pid) ? modelConfigSource(pid, printers) : { printerId: pid, printers };
                  if (!src) return null;
                  return (
                    <PerPrinterConfig
                      key={pid}
                      printerId={src.printerId}
                      printers={src.printers}
                      config={config.perPrinter[pid] ?? defaultPerPrinterCfg()}
                      onChange={patch => onSetPerPrinter(pid, patch)}
                      modelFilaments={isModelKey(pid) ? undefined : modelFilaments}
                      gcode={gcode}
                    />
                  );
                })}
              </div>
            )}
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Orders */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
              <StepNum n={3} done={config.projectId != null} />
              Fulfills project
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              Link this plate to the project its parts belong to. Optional.
            </div>
            <ProjectPicker selectedProjectId={config.projectId} onChange={onSetProject} />
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Job details */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-1)' }}>
              <StepNum n={4} done={!!config.jobName.trim()} />
              Job details
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              How this plate-job appears in the queue.
            </div>
            <div>
              <label className="label">Job name</label>
              <input
                className="input"
                value={config.jobName}
                onChange={e => onSetField({ jobName: e.target.value })}
                placeholder="e.g. PA-CF arm brackets"
              />
            </div>
          </div>

          {embeddedSettings.length > 0 && (
            <>
              <div className="divider" style={{ margin: 0 }} />
              <OverridePanel
                settings={embeddedSettings}
                value={config.confirmedOverrides}
                onChange={onSetOverrides}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SummaryCard
// ============================================================
