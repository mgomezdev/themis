import { useRef, useState } from 'react';
import { commitInstall, discardInstall, previewGithub, previewUpload, type InstallPreview, type PluginSummary } from '../api/plugins';

interface Props {
  /** Installed plugins, to say whether the package replaces one. */
  plugins: PluginSummary[];
  /** A preview already staged by "Check for updates" -> upgrade; skips the source step. */
  initial?: InstallPreview;
  onClose: () => void;
  onInstalled: () => void;
}

const short = (s: string | null, n = 12) => (s ? s.slice(0, n) : '—');

/** Install a plugin: pick a source (upload / public GitHub repo), review exactly what will be installed — source, publisher
 *  (display only, not verified), sha256 — accept the full-trust warning, then confirm. Nothing runs until the admin restarts. */
export function PluginInstallDialog({ plugins, initial, onClose, onInstalled }: Props) {
  const [source, setSource] = useState<'upload' | 'github'>('upload');
  const [repo, setRepo] = useState('');
  const [ref, setRef] = useState('');
  const [subdir, setSubdir] = useState('');
  const file = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<InstallPreview | null>(initial ?? null);
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function review() {
    setError(''); setBusy(true);
    try {
      if (source === 'upload') {
        const f = file.current?.files?.[0];
        if (!f) throw new Error('Choose a .zip, .tar.gz or .tgz file');
        setPreview(await previewUpload(f));
      } else {
        if (!repo.trim()) throw new Error('Enter the repository URL');
        setPreview(await previewGithub({ repo_url: repo.trim(), ...(ref.trim() ? { ref: ref.trim() } : {}), ...(subdir.trim() ? { subdir: subdir.trim() } : {}) }));
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not read the package'); }
    finally { setBusy(false); }
  }

  async function install() {
    if (!preview) return;
    setError(''); setBusy(true);
    try { await commitInstall(preview.token); onInstalled(); }
    catch (e) {
      // The staged package is spent (a refused or failed commit discards it): go back to choosing a source, with the reason.
      setPreview(null); setTrust(false);
      setError(e instanceof Error ? e.message : 'Install failed'); setBusy(false);
    }
  }

  function close() {
    if (busy) return;                                          // a commit in flight must not be discarded under itself
    if (preview) void discardInstall(preview.token);          // an unconfirmed package is not kept
    onClose();
  }

  const existing = preview ? plugins.find(p => p.id === preview.id) : undefined;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
         onClick={e => { if (e.target === e.currentTarget) close(); }}>
      <div className="card" role="dialog" aria-label="Install plugin" style={{ width: 520, maxWidth: '92vw', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="row between" style={{ alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{initial ? 'Upgrade plugin' : 'Install plugin'}</h3>
          <button className="btn ghost icon sm" aria-label="Close" disabled={busy} onClick={close}>&#x2715;</button>
        </div>

        {!preview && (
          <>
            <div className="row gap-2">
              <button className={`btn sm ${source === 'upload' ? 'primary' : ''}`} aria-pressed={source === 'upload'} onClick={() => setSource('upload')}>Upload file</button>
              <button className={`btn sm ${source === 'github' ? 'primary' : ''}`} aria-pressed={source === 'github'} onClick={() => setSource('github')}>GitHub repository</button>
            </div>
            {source === 'upload' ? (
              <label className="col gap-1 small">Plugin archive (.zip, .tar.gz, .tgz)
                <input ref={file} type="file" accept=".zip,.tar.gz,.tgz,application/zip,application/gzip" aria-label="Plugin archive" />
              </label>
            ) : (
              <div className="col gap-2">
                <label className="col gap-1 small">Repository URL
                  <input className="input" value={repo} onChange={e => setRepo(e.target.value)} placeholder="https://github.com/owner/repo" aria-label="Repository URL" />
                </label>
                <div className="row gap-2">
                  <label className="col gap-1 small" style={{ flex: 1 }}>Ref (tag, branch or commit)
                    <input className="input" value={ref} onChange={e => setRef(e.target.value)} placeholder="default branch" aria-label="Ref" />
                  </label>
                  <label className="col gap-1 small" style={{ flex: 1 }}>Subdirectory
                    <input className="input" value={subdir} onChange={e => setSubdir(e.target.value)} placeholder="repo root" aria-label="Subdirectory" />
                  </label>
                </div>
                <div className="tiny muted">Public repositories only. The ref is resolved to a commit and that exact commit is installed.</div>
              </div>
            )}
          </>
        )}

        {preview && (
          <div className="col gap-2" data-testid="install-preview">
            <div>
              <div style={{ fontWeight: 600 }}>{preview.name} <span className="muted small">v{preview.version}</span></div>
              <div className="muted small">{preview.description || preview.kind.replace(/_/g, ' ')}</div>
            </div>
            <dl className="small" style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '4px 12px', margin: 0 }}>
              <dt className="muted">Plugin id</dt><dd style={{ margin: 0 }}>{preview.id}</dd>
              <dt className="muted">Publisher</dt><dd style={{ margin: 0 }}>{preview.publisher ?? 'not stated'} <span className="muted">(not verified)</span></dd>
              <dt className="muted">Source</dt>
              <dd style={{ margin: 0, wordBreak: 'break-all' }}>
                {preview.source === 'github' ? `${preview.source_url}${preview.ref ? ` @ ${preview.ref}` : ''}` : `file ${preview.source_url}`}
              </dd>
              {preview.commit_sha && <><dt className="muted">Commit</dt><dd style={{ margin: 0 }} title={preview.commit_sha}>{short(preview.commit_sha, 12)}</dd></>}
              <dt className="muted">SHA-256</dt><dd style={{ margin: 0, wordBreak: 'break-all', fontFamily: 'monospace', fontSize: 11 }}>{preview.archive_sha256}</dd>
            </dl>
            {existing && <div className="small" data-testid="install-replaces">Replaces the installed v{existing.install?.version ?? existing.version}; the old version is kept for rollback.</div>}
            <div role="alert" className="small" style={{ border: '1px solid var(--warn, #c9a227)', borderRadius: 6, padding: 10 }}>
              <strong>This plugin gets full access to Themis.</strong> It runs inside the Themis process and can read your data, every
              secret and API key, and control your printers. Only install code you trust. It does not run until you restart Themis.
            </div>
            <label className="row gap-2 small" style={{ alignItems: 'center' }}>
              <input type="checkbox" checked={trust} onChange={e => setTrust(e.target.checked)} /> I trust this plugin and its publisher
            </label>
          </div>
        )}

        {error && <div role="alert" className="small" style={{ color: 'var(--err, #d44)' }}>{error}</div>}

        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" disabled={busy} onClick={close}>Cancel</button>
          {!preview
            ? <button className="btn primary" disabled={busy} onClick={() => void review()}>{busy ? 'Checking…' : 'Review'}</button>
            : <button className="btn primary" disabled={busy || !trust} onClick={() => void install()}>{busy ? 'Installing…' : existing ? 'Upgrade' : 'Install'}</button>}
        </div>
      </div>
    </div>
  );
}
