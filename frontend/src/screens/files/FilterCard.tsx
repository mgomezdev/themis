// Files: search box and tag / kind facets.
import { Icons } from '../../components/icons';

export interface FacetGroup { label: string; tags: string[]; }

interface FilterCardProps {
  expanded: boolean;
  onToggle: () => void;
  activeTags: string[];
  setActiveTags: (tags: string[]) => void;
  toggleTag: (tag: string) => void;
  tagCounts: Record<string, number>;
  facetGroups: FacetGroup[];
}

export function FilterCard({
  expanded, onToggle, activeTags, setActiveTags, toggleTag, tagCounts, facetGroups,
}: FilterCardProps) {
  return (
    <div className="card" style={{ padding: expanded ? 14 : '10px 14px' }}>
      <div className="row between" style={{
        marginBottom: expanded ? 8 : 0,
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}>
        <div className="row gap-3" style={{ alignItems: 'center', flex: 1, minWidth: 0, flexWrap: 'wrap' }}>
          <span className="tag-key shrink-0">Filter by tag</span>
          {activeTags.length > 0 ? (
            <div className="row gap-2 wrap">
              {activeTags.map(t => (
                <button key={t} onClick={() => toggleTag(t)}
                        className="row gap-2"
                        style={{
                          padding: '4px 10px',
                          background: 'var(--accent)',
                          color: 'oklch(9% 0.010 55)',
                          border: 'none',
                          borderRadius: 0,
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: 'pointer',
                          alignItems: 'center',
                          whiteSpace: 'nowrap',
                          display: 'inline-flex',
                        }}>
                  {t}
                  <span style={{ width: 12, height: 12, display: 'inline-flex' }}>{Icons.x}</span>
                </button>
              ))}
            </div>
          ) : (
            !expanded && <span className="tiny muted">No filters · click to add</span>
          )}
        </div>
        <div className="row gap-2 shrink-0">
          {activeTags.length > 0 && (
            <button className="btn ghost sm" onClick={() => setActiveTags([])}>
              Clear {activeTags.length}
            </button>
          )}
          <button className="btn ghost icon sm"
                  title={expanded ? 'Hide facets' : 'Show facets'}
                  onClick={onToggle}>
            <span style={{
              display: 'inline-flex',
              transform: expanded ? 'rotate(180deg)' : 'none',
              transition: 'transform 160ms ease',
            }}>{Icons.chevD}</span>
          </button>
        </div>
      </div>

      {expanded && (
        <div className="col gap-3" style={{ marginTop: 4 }}>
          {facetGroups.map(group => {
            if (group.tags.length === 0) return null;
            return (
              <div key={group.label}>
                <div className="tag-key" style={{ marginBottom: 6 }}>{group.label}</div>
                <div className="row gap-2 wrap">
                  {group.tags.map(t => {
                    const on = activeTags.includes(t);
                    const count = tagCounts[t] ?? 0;
                    return (
                      <button key={t} onClick={() => toggleTag(t)} className={on ? 'tone-accent edge' : undefined}
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 6,
                                padding: '4px 10px',
                                borderRadius: 0,
                                ...(on ? {} : { border: '1px solid var(--border-1)' }),      // `on` draws its outline with .edge
                                background: on ? 'var(--accent-tint)' : 'var(--bg-0)',
                                color: on ? 'var(--accent-hi)' : 'var(--text-1)',
                                fontSize: 12,
                                fontWeight: 500,
                                cursor: 'pointer',
                                whiteSpace: 'nowrap',
                              }}>
                        <span style={{
                          width: 12, height: 12, borderRadius: 0,
                          display: 'inline-grid', placeItems: 'center',
                          background: on ? 'var(--accent)' : 'transparent',
                          border: `1.5px solid ${on ? 'var(--accent)' : 'var(--border-2)'}`,
                          color: 'oklch(9% 0.010 55)', flexShrink: 0,
                        }}>
                          {on && (
                            <svg width="8" height="8" viewBox="0 0 24 24" fill="none"
                                 stroke="currentColor" strokeWidth="4">
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          )}
                        </span>
                        {t}
                        <span className="num tiny"
                              style={{ color: on ? 'var(--accent-hi)' : 'var(--text-4)', fontWeight: 500 }}>
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// FilesTabBar
// -------------------------------------------------------------------------
