// frontend/src/components/MaintenanceDueBadge.tsx
export function MaintenanceDueBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="tiny"
      title={`${count} maintenance item${count === 1 ? '' : 's'} due`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 999,
        background: 'var(--warn-bg, rgba(234,179,8,0.12))',
        color: 'var(--warn, #eab308)',
        border: '1px solid var(--warn, #eab308)',
        fontWeight: 500,
      }}
    >
      ⚠ {count} due
    </span>
  );
}
