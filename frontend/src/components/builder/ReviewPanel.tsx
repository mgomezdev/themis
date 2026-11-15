import type { ProjectReview, ReviewCheck } from '../../api/projects';

export type BuilderTab = 'details' | 'items' | 'groups' | 'review';

const PILL: Record<ReviewCheck['level'], { cls: string; label: string }> = {
  error: { cls: 'pill err', label: 'Fix' },
  note: { cls: 'pill warn', label: 'Note' },
  ok: { cls: 'pill ok', label: 'OK' },
};
const TAB_LABEL: Record<ReviewCheck['tab'], string> = { details: 'Details', items: 'Items', groups: 'Groups' };

/** The Review tab's checks (BIZ-363): errors first, each naming (and linking to) the tab that fixes it. */
export function ReviewPanel({ review, loading, error, onGo }: {
  review: ProjectReview | null;
  loading: boolean;
  error: string | null;
  onGo: (tab: BuilderTab, check: ReviewCheck) => void;
}) {
  if (error) return <div role="alert" style={{ color: 'var(--err)', fontSize: 13 }}>Couldn't run the checks: {error}</div>;
  if (!review) return <div className="small muted">{loading ? 'Checking…' : 'No checks yet.'}</div>;
  const order = { error: 0, note: 1, ok: 2 } as const;
  const checks = [...review.checks].sort((a, b) => order[a.level] - order[b.level]);
  return (
    <div className="col gap-2" data-testid="review-checks" aria-busy={loading}>
      <div className="label">Checks</div>
      {checks.map((ch, i) => (
        <div key={i} className="row gap-3" data-testid={`check-${ch.level}`}
             style={{ alignItems: 'center', padding: '8px 10px', border: '1px solid var(--border-1)', borderRadius: 6 }}>
          <span className={PILL[ch.level].cls}>{PILL[ch.level].label}</span>
          <span className="small grow">{ch.message}</span>
          {ch.level === 'error'
            ? <button className="btn sm" onClick={() => onGo(ch.tab, ch)}>Go to {TAB_LABEL[ch.tab]}</button>
            : <span className="tiny muted">—</span>}
        </div>
      ))}
    </div>
  );
}

/** The Builder's section tabs: always clickable, with counts and the Review "N to fix" badge. */
export function BuilderTabs({ tab, onTab, counts }: {
  tab: BuilderTab;
  onTab: (t: BuilderTab) => void;
  counts: { items: number; groups: number; toFix: number | null };
}) {
  const tabs: [BuilderTab, string, string | null][] = [
    ['details', 'Details', null],
    ['items', 'Items', String(counts.items)],
    ['groups', 'Groups', String(counts.groups)],
    ['review', 'Review', counts.toFix ? `${counts.toFix} to fix` : null],
  ];
  return (
    <div className="row gap-1" role="tablist" aria-label="Project sections" style={{ borderBottom: '1px solid var(--border-1)' }}>
      {tabs.map(([key, label, n]) => (
        <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => onTab(key)}
                style={{ padding: '8px 14px', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: 14,
                         color: tab === key ? 'var(--text-1)' : 'var(--text-3)', fontWeight: tab === key ? 600 : 400,
                         borderBottom: tab === key ? '2px solid var(--accent)' : '2px solid transparent' }}>
          {label}
          {n && <span className={key === 'review' ? 'pill err' : 'tiny muted'} style={{ marginLeft: 6 }}>{n}</span>}
        </button>
      ))}
    </div>
  );
}
