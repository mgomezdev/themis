// Settings → About: version and build details.
import { PageHeader } from '../../components/settingsUi';
import { useBuildInfo, shortSha } from '../../api/version';

function AboutTile({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div style={{ padding: '12px 14px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 8 }}>
      <div className="tag-key">{k}</div>
      <div style={{ marginTop: 4, fontSize: 14, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', color: 'var(--text-1)' }}>{v}</div>
    </div>
  );
}

export function AboutPage() {
  const build = useBuildInfo();
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="About Themis" />
      <div className="row gap-4" style={{ alignItems: 'center', marginBottom: 24 }}>
        <div style={{
          width: 64, height: 64, borderRadius: 14,
          background: 'linear-gradient(135deg, var(--accent-hi), var(--accent-lo))',
          boxShadow: '0 0 24px var(--accent-glow)',
          border: '1px solid var(--border-2)',
          display: 'grid', placeItems: 'center',
          color: 'white', fontWeight: 700, fontSize: 24, letterSpacing: '-0.02em',
          fontFamily: 'var(--font-sans)',
        }}>θ</div>
        <div className="col">
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>themis<span style={{ color: 'var(--text-3)', fontWeight: 400 }}>.farm</span></div>
          <div className="small muted">Workshop print-farm manager · single-user build</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <AboutTile k="Version" v={__APP_VERSION__} mono />
        <AboutTile k="Commit" v={build ? shortSha(build.git_sha) : '…'} mono />
      </div>
    </div>
  );
}

// =========================================================================
// Fleet backup / restore page
// =========================================================================
