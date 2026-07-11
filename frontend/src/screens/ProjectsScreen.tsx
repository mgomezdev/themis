import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty, Progress } from '../components/ui';
import { useProjects, deleteProject, generateProject, type Project } from '../api/projects';

type Filter = 'all' | 'pending' | 'active' | 'completed';

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function projectFilter(p: Project, f: Filter): boolean {
  switch (f) {
    case 'pending':   return p.jobs_total === 0;
    case 'active':    return p.jobs_total > 0 && p.jobs_complete < p.jobs_total;
    case 'completed': return p.jobs_total > 0 && p.jobs_complete === p.jobs_total;
    default:          return true;
  }
}

function summarise(p: Project) {
  const parts = p.items.length;
  const copies = p.items.reduce((s, i) => s + i.quantity, 0);
  if (parts === 0) return 'No parts yet';
  return `${parts} part${parts !== 1 ? 's' : ''} · ${copies} cop${copies !== 1 ? 'ies' : 'y'}`;
}

function ProjectCard({
  project,
  onOpen,
  onEdit,
  onDelete,
  onGenerate,
}: {
  project: Project;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onGenerate: () => void;
}) {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const dueStr = formatDate(project.due_date);
  const isOverdue = project.due_date ? new Date(project.due_date) < new Date() : false;

  async function handleGenerate(e: React.MouseEvent) {
    e.stopPropagation();
    setGenerating(true);
    setError('');
    try {
      await onGenerate();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div
      className="card"
      onClick={onOpen}
      style={{
        display: 'flex', flexDirection: 'column', gap: 10, padding: 16,
        cursor: 'pointer', transition: 'border-color 0.15s',
      }}
    >
      {/* Badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        {project.order_type === 'customer' && (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 10,
            background: 'var(--bg-3)', color: 'var(--text-3)',
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>Customer</span>
        )}
        {project.on_hold && (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 10,
            background: 'rgba(239,160,0,0.15)', color: 'var(--warn)',
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>On hold</span>
        )}
        <span style={{ fontSize: 10, color: 'var(--text-4)', marginLeft: 'auto' }}>#{project.id}</span>
      </div>

      {/* Name + customer */}
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-1)' }}>{project.name}</div>
        {project.customer && (
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 1 }}>{project.customer}</div>
        )}
        <div style={{ fontSize: 12, color: 'var(--text-4)', marginTop: 3 }}>{summarise(project)}</div>
      </div>

      {dueStr && (
        <div style={{ fontSize: 11, color: isOverdue ? 'var(--err)' : 'var(--text-3)' }}>
          Due {dueStr}
        </div>
      )}

      {project.jobs_total > 0 && (
        <div>
          <Progress
            value={project.jobs_total > 0 ? (project.jobs_complete / project.jobs_total) * 100 : 0}
            tone={project.jobs_complete === project.jobs_total ? 'ok' : undefined}
          />
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
            {project.jobs_complete} / {project.jobs_total} jobs
          </div>
        </div>
      )}

      {error && <div style={{ fontSize: 11, color: 'var(--err)' }}>{error}</div>}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 6 }} onClick={e => e.stopPropagation()}>
        <button className="btn sm" onClick={onEdit} style={{ flex: 1 }}>Edit</button>
        <button
          className="btn primary sm"
          onClick={handleGenerate}
          disabled={generating || project.items.length === 0}
          style={{ flex: 1 }}
        >
          {generating ? 'Generating…' : 'Generate'}
        </button>
        <button className="btn ghost icon sm" onClick={e => { e.stopPropagation(); onDelete(); }} title="Delete">
          {Icons.trash}
        </button>
      </div>
    </div>
  );
}

export function ProjectsScreen() {
  const navigate = useNavigate();
  const { projects, refetch } = useProjects();
  const [filter, setFilter] = useState<Filter>('all');

  const visible = projects.filter(p => projectFilter(p, filter));

  const filterCounts: Record<Filter, number> = {
    all:       projects.length,
    pending:   projects.filter(p => projectFilter(p, 'pending')).length,
    active:    projects.filter(p => projectFilter(p, 'active')).length,
    completed: projects.filter(p => projectFilter(p, 'completed')).length,
  };

  async function handleDelete(id: number) {
    if (!confirm('Delete this project?')) return;
    await deleteProject(id);
    refetch();
  }

  async function handleGenerate(id: number) {
    await generateProject(id);
    refetch();
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 4 }}>
        {(['all', 'pending', 'active', 'completed'] as Filter[]).map(f => (
          <button
            key={f}
            className={`btn sm ${filter === f ? 'primary' : 'ghost'}`}
            onClick={() => setFilter(f)}
            style={{ textTransform: 'capitalize' }}
          >
            {f}
            {filterCounts[f] > 0 && (
              <span style={{ marginLeft: 5, opacity: 0.7 }}>({filterCounts[f]})</span>
            )}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        projects.length === 0 ? (
          <Empty
            icon={Icons.layers}
            title="No projects yet — create one to start batching parts."
          />
        ) : (
          <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-4)', fontSize: 13 }}>
            No {filter} projects
          </div>
        )
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 16,
        }}>
          {visible.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => navigate(`/projects/${p.id}`)}
              onEdit={() => navigate(`/projects/${p.id}/edit`)}
              onDelete={() => handleDelete(p.id)}
              onGenerate={() => handleGenerate(p.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
