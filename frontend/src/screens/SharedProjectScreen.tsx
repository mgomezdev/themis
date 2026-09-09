import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getPublicProject, ShareNotFoundError, type PublicProject } from '../api/public';
import { fmtDate, fmtDuration } from '../data/helpers';

export function SharedProjectScreen() {
  const { token } = useParams<{ token: string }>();
  const [project, setProject] = useState<PublicProject | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    setProject(null);
    setNotFound(false);
    setLoadError(false);
    getPublicProject(token)
      .then(p => { if (alive) setProject(p); })
      .catch(e => {
        if (!alive) return;
        if (e instanceof ShareNotFoundError) setNotFound(true);
        else setLoadError(true);
      });
    return () => { alive = false; };
  }, [token]);

  const shellStyle: React.CSSProperties = {
    minHeight: '100vh', display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
    padding: '48px 16px', background: 'var(--bg-0)',
  };
  const cardStyle: React.CSSProperties = {
    width: '100%', maxWidth: 640, background: 'var(--bg-1)', border: '1px solid var(--border-1)',
    borderRadius: 12, padding: 28,
  };

  if (notFound) {
    return (
      <div style={shellStyle}>
        <div style={cardStyle}>
          <p style={{ color: 'var(--text-3)', margin: 0 }}>
            This link is invalid or has been revoked.
          </p>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={shellStyle}>
        <div style={cardStyle}>
          <p style={{ color: 'var(--text-3)', margin: 0 }}>
            Couldn't load this page. Please try again.
          </p>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div style={shellStyle}>
        <div style={cardStyle}>
          <p style={{ color: 'var(--text-3)', margin: 0 }}>Loading…</p>
        </div>
      </div>
    );
  }

  const dueStr = fmtDate(project.due_date);

  return (
    <div style={shellStyle}>
      <div style={cardStyle}>
        <h1 style={{ margin: '0 0 4px', fontSize: 20, fontWeight: 700, color: 'var(--text-1)' }}>
          {project.name}
        </h1>
        {project.customer && (
          <div style={{ fontSize: 14, color: 'var(--text-3)', marginBottom: 4 }}>{project.customer}</div>
        )}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-4)', marginBottom: 16 }}>
          {dueStr && <span>Due {dueStr}</span>}
          {project.on_hold && <span style={{ color: 'var(--warn)' }}>On hold</span>}
        </div>

        <div style={{ fontSize: 14, color: 'var(--text-2)', marginBottom: 16 }}>
          {project.jobs_complete} of {project.jobs_total} printed
          {project.estimate_seconds_remaining != null && (
            <span style={{ color: 'var(--text-4)' }}> · ~{fmtDuration(project.estimate_seconds_remaining)} remaining</span>
          )}
        </div>

        {project.items.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Items</h2>
            {project.items.map((item, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }}>
                <span>{item.name}</span>
                <span style={{ color: 'var(--text-4)' }}>{item.quantity_completed} / {item.quantity}</span>
              </div>
            ))}
          </div>
        )}

        {project.parts.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Parts</h2>
            {project.parts.map((part, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }}>
                <span>{part.name}</span>
                <span style={{ color: 'var(--text-4)' }}>x{part.quantity}</span>
              </div>
            ))}
          </div>
        )}

        {project.links.length > 0 && (
          <div>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Links</h2>
            {project.links.map((link, i) => (
              <div key={i} style={{ fontSize: 13, padding: '4px 0' }}>
                <a href={link.url} target="_blank" rel="noreferrer">{link.label || link.url}</a>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
