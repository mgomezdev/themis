// The Builder's toasts: "Saved" (bottom right) and "Removed … Undo" (bottom left).
import { Icons } from '../icons';

export function SavedToast() {
  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
      background: 'var(--ok)', color: '#fff',
      padding: '10px 18px', borderRadius: 8,
      display: 'flex', alignItems: 'center', gap: 8,
      fontSize: 14, fontWeight: 500,
      boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
      pointerEvents: 'none',
    }}>
      {Icons.check} Saved
    </div>
  );
}

export function UndoToast({ label, onUndo }: { label: string; onUndo: () => void }) {
  return (
    <div style={{
      position: 'fixed', bottom: 24, left: 24, zIndex: 1000,
      background: 'var(--bg-1)', border: '1px solid var(--border-1)', color: 'var(--text-1)',
      padding: '10px 12px 10px 16px', borderRadius: 8,
      display: 'flex', alignItems: 'center', gap: 10,
      fontSize: 13, boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
    }}>
      <span>Removed {label}</span>
      <button className="btn sm" onClick={onUndo}>Undo</button>
    </div>
  );
}
