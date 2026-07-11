import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icons } from '../components/icons';
import { Empty, Progress } from '../components/ui';
import { useProjects, deleteProject, generateProject, type Project } from '../api/projects';

function summarise(p: Project) {
  const parts = p.items.length;
  const copies = p.items.reduce((s, i) => s + i.quantity, 0);
  if (parts === 0) return 'No parts yet';
  return `${parts} part${parts !== 1 ? 's' : ''} · ${copies} cop${copies !== 1 ? 'ies' : 'y'}`;
}

function ProjectCard({
  project,
  onEdit,
  onDelete,
  onGenerate,
}: {
  project: Project;
  onEdit: () => void;
  onDelete: () => void;
  onGenerate: () => void;
}) {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

  async function handleGenerate() {
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
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
        {project.result_file_id ? (
          <div style={{
            height: 100, borderRadius: 6, background: 'var(--bg-3)',
            display: 'grid', placeItems: 'center', marginBottom: 4,
            color: 'var(--text-4)', fontSize: 12,
          }}>
            {Icons.layers}
          </div>
        ) : (
          <div style={{
            height: 100, borderRadius: 6, background: 'var(--bg-3)',
            display: 'grid', placeItems: 'center', marginBottom: 4,
            color: 'var(--text-4)', fontSize: 12,
          }}>
            <span style={{ fontSize: 11 }}>Not yet arranged</span>
          </div>
        )}
        <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-1)' }}>{project.name}</div>
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{summarise(project)}</div>
        {project.jobs_total > 0 && (
          <div style={{ marginTop: 4 }}>
            <Progress value={project.jobs_total > 0 ? (project.jobs_complete / project.jobs_total) * 100 : 0}
                      tone={project.jobs_complete === project.jobs_total ? 'ok' : undefined} />
            <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
              {project.jobs_complete} / {project.jobs_total} jobs complete
            </div>
          </div>
        )}
        {error && <div style={{ fontSize: 11, color: 'var(--err)', marginTop: 2 }}>{error}</div>}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="btn sm" onClick={onEdit} style={{ flex: 1 }}>Edit</button>
        <button
          className="btn primary sm"
          onClick={handleGenerate}
          disabled={generating || project.items.length === 0}
          style={{ flex: 1 }}
        >
          {generating ? 'Generating…' : 'Generate'}
        </button>
        <button className="btn ghost icon sm" onClick={onDelete} title="Delete project">
          {Icons.trash}
        </button>
      </div>
    </div>
  );
}

export function ProjectsScreen() {
  const navigate = useNavigate();
  const { projects, refetch } = useProjects();

  async function handleDelete(id: number) {
    if (!confirm('Delete this project?')) return;
    await deleteProject(id);
    refetch();
  }

  async function handleGenerate(id: number) {
    await generateProject(id);
    refetch();
    navigate('/queue');
  }

  return (
    <div style={{ padding: '0 2px' }}>
      {projects.length === 0 ? (
        <Empty
          icon={Icons.layers}
          title="No projects yet — create one to start batching parts."
        />
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 16,
        }}>
          {projects.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              onEdit={() => navigate(`/projects/${p.id}`)}
              onDelete={() => handleDelete(p.id)}
              onGenerate={() => handleGenerate(p.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
