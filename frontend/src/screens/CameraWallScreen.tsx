import { useEffect, useMemo, useState } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { withKeyParam } from '../api/client';
import { useFleetData } from '../api/fleet';
import { Icons } from '../components/icons';
import { StatusPill, VideoTile } from '../components/ui';
import { SEVERITY_COLOR } from '../lib/severity';
import type { Printer } from '../data/types';

type Filter = 'all' | 'printing' | 'paused' | 'error' | 'idle' | 'offline';
const FILTERS: Filter[] = ['all', 'printing', 'paused', 'error', 'idle', 'offline'];
const COLUMNS = [2, 3, 4, 6, 8, 10];
/** Browsers hold ~6 HTTP/1.1 connections per origin and each live MJPEG <img> keeps one open, so live tiles are
 *  capped; every other tile polls snapshots (the server shares those between viewers). */
/** Over HTTP/1.1 only a couple may stream (the rest of the page needs connections too); HTTP/2 multiplexes, so more. */
function multiplexed(): boolean {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    return /^h[23]/.test(nav?.nextHopProtocol ?? '');
  } catch { return false; }
}
export const LIVE_LIMITS_H1 = [0, 2, 4];
export const LIVE_LIMITS_H2 = [0, 4, 8, 16, 32];
const SNAPSHOT_MS = 5000;

interface Prefs { columns: number; live: number; filter: Filter }
const STORAGE_KEY = 'themis.cameraWall';

function loadPrefs(limits: number[]): Prefs {
  const DEFAULTS: Prefs = { columns: 4, live: limits.includes(8) ? 8 : 2, filter: 'all' };
  try {
    const p = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as Partial<Prefs>;
    return {
      columns: COLUMNS.includes(p.columns as number) ? p.columns! : DEFAULTS.columns,
      live: limits.includes(p.live as number) ? p.live! : DEFAULTS.live,
      filter: FILTERS.includes(p.filter as Filter) ? p.filter! : DEFAULTS.filter,
    };
  } catch { return DEFAULTS; }
}

/** Printers that have a camera, filtered; the ones that get a live stream first: printing, then paused, then the rest. */
export function planWall(printers: Printer[], filter: Filter, liveLimit: number) {
  const shown = printers.filter(p => p.capabilities.includes('camera') && (filter === 'all' || p.status === filter));
  const rank = (p: Printer) => (p.status === 'printing' ? 0 : p.status === 'paused' ? 1 : p.status === 'error' ? 2 : 3);
  const liveIds = new Set(
    shown.filter(p => p.status !== 'offline').map((p, i) => ({ p, i }))
      .sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i).slice(0, liveLimit).map(x => x.p.id),
  );
  return { shown, liveIds };
}

function LiveFeed({ printer, onFail }: { printer: Printer; onFail: () => void }) {
  return (
    <div className="video live">
      <img src={withKeyParam(`/api/v1/printers/${printer.id}/camera`)} alt=""
           style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} onError={onFail} />
    </div>
  );
}

function WallTile({ printer: p, live }: { printer: Printer; live: boolean }) {
  const inRouter = useInRouterContext();
  const [liveFailed, setLiveFailed] = useState(false);
  useEffect(() => setLiveFailed(false), [live, p.id]);
  const asLive = live && !liveFailed;
  const to = `/fleet/${p.id}/console`;
  const label = <>{Icons.alert} {p.alarmCount}</>;
  const body = (
    <>
      <div style={{ aspectRatio: '16 / 9', position: 'relative', overflow: 'hidden', background: '#000' }}>
        {asLive
          ? <LiveFeed printer={p} onFail={() => setLiveFailed(true)} />
          : <VideoTile live={p.status !== 'offline'} printerId={p.id} status={p.status} noSnapshotsWhileIdle={p.noSnapshotsWhileIdle}
                       intervalMs={SNAPSHOT_MS + (Number(p.id) % 10) * 130} />}   /* staggered so 40 tiles don't all tick at once */
        <div style={{ position: 'absolute', top: 6, left: 6, right: 6, display: 'flex', justifyContent: 'space-between',
                      gap: 6, zIndex: 4, pointerEvents: 'none' }}>
          <span className="tiny" style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', borderRadius: 4, padding: '1px 6px',
                                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
          <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            {!!p.alarmCount && p.alarmSeverity && (
              <span data-testid="wall-alarm" title={`${p.alarmCount} unacknowledged alarm${p.alarmCount === 1 ? '' : 's'} (worst: ${p.alarmSeverity})`}
                    style={{ background: 'rgba(0,0,0,0.6)', color: SEVERITY_COLOR[p.alarmSeverity], border: `1px solid ${SEVERITY_COLOR[p.alarmSeverity]}`,
                             borderRadius: 999, padding: '0 6px', fontSize: 11, fontWeight: 600 }}>{label}</span>
            )}
            {asLive && <StatusPill status={p.status} />}      {/* snapshot tiles get theirs from VideoTile */}
          </span>
        </div>
        <span className="tiny" data-testid={asLive ? 'wall-live' : 'wall-snapshot'}
              style={{ position: 'absolute', bottom: 6, left: 6, zIndex: 4, background: 'rgba(0,0,0,0.6)', color: asLive ? '#f87171' : '#cbd5e1',
                       borderRadius: 4, padding: '0 5px' }}>{asLive ? '● LIVE' : 'snapshot'}</span>
      </div>
    </>
  );
  const style = { display: 'block', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)', textDecoration: 'none', color: 'inherit' };
  return inRouter
    ? <Link to={to} aria-label={`Open ${p.name} console`} style={style} data-testid="wall-tile">{body}</Link>
    : <a href={to} aria-label={`Open ${p.name} console`} style={style} data-testid="wall-tile">{body}</a>;
}

export function CameraWallScreen() {
  const [printers] = useFleetData();
  const [limits] = useState(() => (multiplexed() ? LIVE_LIMITS_H2 : LIVE_LIMITS_H1));
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs(limits));
  const set = (patch: Partial<Prefs>) => setPrefs(prev => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* private mode: keep it in memory */ }
    return next;
  });
  const withCamera = useMemo(() => printers.filter(p => p.capabilities.includes('camera')), [printers]);
  const { shown, liveIds } = useMemo(() => planWall(printers, prefs.filter, prefs.live), [printers, prefs.filter, prefs.live]);
  const count = (f: Filter) => f === 'all' ? withCamera.length : withCamera.filter(p => p.status === f).length;

  return (
    <div className="page" data-testid="camera-wall">
      <div className="row gap-3" style={{ alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        {FILTERS.map(f => (
          <button key={f} className={`btn sm${prefs.filter === f ? ' primary' : ''}`} aria-pressed={prefs.filter === f}
                  onClick={() => set({ filter: f })}>{f[0].toUpperCase() + f.slice(1)} · {count(f)}</button>
        ))}
        <span style={{ flex: 1 }} />
        <label className="small muted" htmlFor="wall-cols">Columns</label>
        <select id="wall-cols" className="input" style={{ width: 'auto' }} value={prefs.columns}
                onChange={e => set({ columns: Number(e.target.value) })}>
          {COLUMNS.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <label className="small muted" htmlFor="wall-live">Live streams</label>
        <select id="wall-live" className="input" style={{ width: 'auto' }} value={prefs.live}
                onChange={e => set({ live: Number(e.target.value) })}>
          {limits.map(c => <option key={c} value={c}>{c === 0 ? 'none (snapshots)' : `up to ${c}`}</option>)}
        </select>
      </div>
      {shown.length === 0 ? (
        <div className="muted" role="status">{withCamera.length === 0 ? 'No printers with a camera.' : 'No printers match this filter.'}</div>
      ) : (
        <div data-testid="wall-grid" style={{ display: 'grid', gap: 8, gridTemplateColumns: `repeat(${prefs.columns}, minmax(0, 1fr))` }}>
          {shown.map(p => <WallTile key={p.id} printer={p} live={liveIds.has(p.id)} />)}
        </div>
      )}
      <p className="tiny muted" style={{ marginTop: 12 }}>
        Only a few tiles stream live (browsers cap connections per site); the rest refresh every {SNAPSHOT_MS / 1000} s.
      </p>
    </div>
  );
}
