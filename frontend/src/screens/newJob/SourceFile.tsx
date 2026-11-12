// New job, step 1: the upload drop zone and the chosen-file card.
import React from 'react';
import { fmtBytes } from '../../data/helpers';
import { Icons } from '../../components/icons';
import { FileInfo } from './model';

interface DropzoneProps {
  dragOver: boolean;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onClick: () => void;
}

export function Dropzone({ dragOver, onDragEnter, onDragLeave, onDragOver, onDrop, onClick }: DropzoneProps) {
  return (
    <div
      onClick={onClick}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      style={{
        border: `1.5px dashed ${dragOver ? 'var(--accent)' : 'var(--border-2)'}`,
        background: dragOver ? 'var(--accent-glow)' : 'var(--bg-0)',
        borderRadius: 12,
        padding: '32px 24px',
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'background 120ms, border-color 120ms',
      }}>
      <div style={{
        width: 48, height: 48, borderRadius: 12,
        background: 'var(--bg-2)', color: dragOver ? 'var(--accent-hi)' : 'var(--text-3)',
        display: 'grid', placeItems: 'center', margin: '0 auto 12px',
      }}>
        {React.cloneElement(Icons.upload as React.ReactElement<{ size?: number }>, { size: 22 })}
      </div>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-1)' }}>
        Drop a .3mf, .stl, .gcode or .gcode.3mf file
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        Or click to browse · multi-plate 3MFs supported · .gcode / .gcode.3mf skip slicing
      </div>
    </div>
  );
}

// ============================================================
// FileCard
// ============================================================

export function FileCard({ file, plateCount, selectedCount, onClear }: {
  file: FileInfo;
  plateCount: number;
  selectedCount: number;
  onClear: () => void;
}) {
  return (
    <div className="row gap-3" style={{
      padding: '14px 16px', background: 'var(--bg-0)',
      border: '1px solid var(--border-1)', borderRadius: 10,
    }}>
      <div style={{
        width: 44, height: 44, borderRadius: 8,
        background: 'linear-gradient(135deg, var(--bg-2), var(--border-2))',
        color: 'white',
        display: 'grid', placeItems: 'center',
        fontWeight: 700, fontSize: 10.5,
        fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
        border: '1px solid var(--border-2)',
      }}>
        {file.type.toUpperCase()}
      </div>
      <div className="col grow min-0">
        <div className="small" style={{
          fontWeight: 500, color: 'var(--text-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {file.name}
        </div>
        <div className="tiny muted" style={{ marginTop: 2 }}>
          {plateCount === 1
            ? 'Single plate'
            : `${plateCount} plates · ${selectedCount} selected → ${selectedCount} job${selectedCount === 1 ? '' : 's'}`}
          {file.size ? ` · ${fmtBytes(file.size)}` : ''}
        </div>
      </div>
      <button className="btn ghost sm" onClick={onClear}>{Icons.x} Replace</button>
    </div>
  );
}

// ============================================================
// QueueToggle
// ============================================================
