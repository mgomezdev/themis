import React, { useState, useMemo } from 'react';
import { FILAMENTS, PRINTERS, getPrinter } from '../data/mock';
import { matTypeBg, matTypeFg, matTypeBorder } from '../data/helpers';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import type { Filament, FilamentProfile } from '../data/types';

// ---------- spool swatch ----------

function SpoolSwatch({ color, size = 40 }: { color: string; size?: number }) {
  const s = size;
  const innerHole = Math.round(s * 0.22);
  return (
    <div style={{
      width: s, height: s,
      borderRadius: '50%',
      background: `radial-gradient(circle at 50% 50%, ${color} 0%, ${color} 56%, rgba(255,255,255,0.10) 57%, rgba(255,255,255,0.10) 60%, ${color} 61%, ${color} 78%, rgba(0,0,0,0.30) 79%, rgba(0,0,0,0.30) 82%, ${color} 83%)`,
      border: '1px solid var(--border-2)',
      boxShadow: `inset 0 0 0 1px rgba(255,255,255,0.06), 0 4px 10px -4px ${color}55`,
      position: 'relative',
      flexShrink: 0,
      display: 'grid',
      placeItems: 'center',
    }}>
      <div style={{
        width: innerHole, height: innerHole,
        borderRadius: '50%',
        background: 'var(--bg-2)',
        border: '1px solid var(--border-2)',
        boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.6)',
      }} />
    </div>
  );
}

// ---------- star / favorite toggle ----------

function Star({ filled }: { filled: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24"
         fill={filled ? 'currentColor' : 'none'}
         stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"
         style={{ display: 'inline-block' }}>
      <path d="M12 2l3.1 6.3 7 1-5 4.9 1.2 6.8L12 17.8 5.7 21l1.2-6.8-5-4.9 7-1z" />
    </svg>
  );
}

function FavoriteToggle({ filled, onClick, size = 16 }: { filled: boolean; onClick?: () => void; size?: number }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick?.(); }}
      title={filled ? 'Unpin from favorites' : 'Pin to favorites'}
      aria-pressed={filled}
      style={{
        background: 'transparent',
        border: 'none',
        padding: 4,
        margin: 0,
        cursor: 'pointer',
        color: filled ? 'var(--warn)' : 'var(--text-4)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 4,
        flexShrink: 0,
        transition: 'color 120ms',
      }}>
      <svg width={size} height={size} viewBox="0 0 24 24"
           fill={filled ? 'currentColor' : 'none'}
           stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
        <path d="M12 2l3.1 6.3 7 1-5 4.9 1.2 6.8L12 17.8 5.7 21l1.2-6.8-5-4.9 7-1z" />
      </svg>
    </button>
  );
}

// ---------- summary card ----------

function FilSummary({ label, value, sub, pillTone }: { label: string; value: number; sub?: string; pillTone?: string }) {
  return (
    <div className="card" style={{ minWidth: 180, padding: '14px 16px' }}>
      <div className="tag-key">{label}</div>
      <div className="row gap-2" style={{ marginTop: 6, alignItems: 'baseline', whiteSpace: 'nowrap' }}>
        <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>{value}</div>
        {pillTone && <span className={`pill ${pillTone}`} style={{ fontSize: 10 }}><span className="dot" />pinned</span>}
      </div>
      {sub && <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ---------- filter chip ----------

function FilChip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button onClick={onClick}
            className={`btn sm ${active ? 'primary' : ''}`}
            style={active ? undefined : { background: 'transparent', borderColor: 'var(--border-1)' }}>
      {children}
    </button>
  );
}

// ---------- spec helpers ----------

function SpecKv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="col">
      <div className="tag-key">{k}</div>
      <div style={{ marginTop: 4, fontSize: 13, color: 'var(--text-1)' }}>{v}</div>
    </div>
  );
}

function ProfileStat({ k, v }: { k: string; v: string }) {
  return (
    <div className="col">
      <div className="tag-key" style={{ fontSize: 9.5 }}>{k}</div>
      <div className="num" style={{ marginTop: 2, fontSize: 12, color: 'var(--text-2)' }}>{v}</div>
    </div>
  );
}

// ---------- filament card ----------

interface FilamentCardProps {
  filament: Filament;
  selected: boolean;
  onClick: () => void;
  onToggleFavorite: () => void;
}

function FilamentCard({ filament: f, selected, onClick, onToggleFavorite }: FilamentCardProps) {
  const printerIds = useMemo(() => {
    const ids = new Set((f.profiles ?? []).map((p: FilamentProfile) => p.printerId));
    return Array.from(ids);
  }, [f]);

  return (
    <div className={`card ${selected ? 'raised' : ''}`}
         onClick={onClick}
         style={{
           padding: '12px 16px',
           cursor: 'pointer',
           borderColor: selected ? 'var(--accent)' : undefined,
           boxShadow: selected ? '0 0 0 1px var(--accent), 0 8px 24px -8px var(--accent-glow)' : undefined,
           position: 'relative',
         }}>
      <div className="row gap-3" style={{ alignItems: 'center' }}>
        <SpoolSwatch color={f.color} size={40} />

        <div className="col" style={{ flex: '1 1 0', minWidth: 0, gap: 3 }}>
          <div className="row gap-2" style={{ alignItems: 'baseline', minWidth: 0 }}>
            <div style={{
              fontSize: 14, fontWeight: 500, color: 'var(--text-1)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              maxWidth: '100%',
            }}>{f.name}</div>
            {f.favorite && <FavoriteToggle filled onClick={onToggleFavorite} size={14} />}
          </div>
          <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {f.manufacturer} · {f.colorName} · {f.diameter}mm
          </div>
        </div>

        <div style={{ width: 110, flexShrink: 0 }}>
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <span className="pill" style={{
              fontSize: 11, padding: '3px 8px',
              background: matTypeBg(f.type), color: matTypeFg(f.type),
              borderColor: matTypeBorder(f.type),
            }}>
              {f.type}
            </span>
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>{f.subtype}</div>
        </div>

        <div style={{ width: 130, flexShrink: 0 }}>
          <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
            {PRINTERS.map(p => (
              <span key={p.id} className={`elig ${printerIds.includes(p.id) ? 'on' : 'off'}`} title={p.name}>
                {p.badge}
              </span>
            ))}
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>
            <span className="num" style={{ color: 'var(--text-2)' }}>{f.profiles.length}</span>{' '}
            profile{f.profiles.length === 1 ? '' : 's'}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- detail panel ----------

interface FilamentDetailPanelProps {
  filament: Filament;
  onClose: () => void;
  onToggleFavorite: () => void;
}

function FilamentDetailPanel({ filament: f, onClose, onToggleFavorite }: FilamentDetailPanelProps) {
  return (
    <div className="card" style={{ position: 'sticky', top: 0, padding: 18, height: 'fit-content' }}>
      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="mono tiny muted">{f.id}</div>
        <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
      </div>

      <div className="row gap-3" style={{ alignItems: 'center' }}>
        <SpoolSwatch color={f.color} size={48} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="row gap-2" style={{ alignItems: 'center' }}>
            <div style={{ fontSize: 16, fontWeight: 500, lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {f.name}
            </div>
            <FavoriteToggle filled={!!f.favorite} onClick={onToggleFavorite} />
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {f.manufacturer} · {f.colorName}
          </div>
        </div>
      </div>

      <div className="row gap-2" style={{ marginTop: 12, flexWrap: 'wrap' }}>
        <span className="pill" style={{
          fontSize: 11, padding: '3px 8px',
          background: matTypeBg(f.type), color: matTypeFg(f.type),
          borderColor: matTypeBorder(f.type),
        }}>{f.type}</span>
        <span className="pill idle" style={{ fontSize: 11 }}>{f.subtype}</span>
        <span className="pill idle" style={{ fontSize: 11 }}>{f.diameter}mm</span>
      </div>

      <div className="row gap-4" style={{ marginTop: 14 }}>
        <SpecKv k="Dry @"    v={<span className="num">{f.dryTemp}°C</span>} />
        <SpecKv k="Profiles" v={<span className="num">{f.profiles.length}</span>} />
        <SpecKv k="Vendors"  v={<span className="num">{f.purchaseLinks.length}</span>} />
      </div>

      <div className="divider" />

      <div className="tag-key">Print profiles</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {f.profiles.map((p, i) => {
          const printer = getPrinter(p.printerId);
          return (
            <div key={i} style={{
              padding: '10px 12px',
              background: 'var(--bg-1)',
              borderRadius: 8,
              border: '1px solid var(--border-1)',
            }}>
              <div className="row between" style={{ alignItems: 'flex-start' }}>
                <div className="col" style={{ flex: 1, minWidth: 0 }}>
                  <div className="row gap-2" style={{ alignItems: 'center' }}>
                    <span className="elig on" style={{ flexShrink: 0 }}>{printer?.badge ?? '?'}</span>
                    <div className="small" style={{ fontWeight: 500, color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.name}
                    </div>
                  </div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    {printer?.nickname ?? printer?.name ?? '—'}
                  </div>
                </div>
              </div>
              <div className="row gap-3" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                <ProfileStat k="Nozzle" v={p.nozzle} />
                <ProfileStat k="Hotend" v={`${p.hotendTemp}°C`} />
                <ProfileStat k="Bed"    v={`${p.bedTemp}°C`} />
                <ProfileStat k="Layer"  v={`${p.layerHeight}mm`} />
              </div>
              {p.notes && (
                <div className="tiny" style={{ marginTop: 8, color: 'var(--text-3)', lineHeight: 1.5 }}>
                  {p.notes}
                </div>
              )}
            </div>
          );
        })}

        <button
          onClick={(e) => { e.stopPropagation(); }}
          style={{
            padding: '12px',
            background: 'transparent',
            borderRadius: 8,
            border: '1px dashed var(--border-2)',
            color: 'var(--text-3)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            fontFamily: 'var(--font-sans)',
            fontSize: 12.5,
            transition: 'background 120ms, color 120ms, border-color 120ms',
          }}>
          {React.cloneElement(Icons.plus as React.ReactElement<{ size?: number }>, { size: 14 })}
          <span>New profile</span>
        </button>
      </div>

      <div className="divider" />

      <div className="tag-key">Purchase</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {f.purchaseLinks.map((lnk, i) => (
          <a key={i} href={lnk.url} target="_blank" rel="noreferrer"
             className="row between"
             style={{
               padding: '8px 12px',
               background: 'var(--bg-1)',
               borderRadius: 8,
               border: '1px solid var(--border-1)',
               textDecoration: 'none',
               color: 'var(--text-1)',
             }}
             onClick={e => e.stopPropagation()}>
            <div className="col" style={{ minWidth: 0, flex: 1 }}>
              <div className="small" style={{ fontWeight: 500 }}>{lnk.vendor}</div>
              <div className="tiny muted" style={{
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                maxWidth: '100%',
              }}>
                {lnk.url.replace(/^https?:\/\//, '')}
              </div>
            </div>
            <span style={{ color: 'var(--text-3)', display: 'inline-flex', flexShrink: 0 }}>
              {React.cloneElement(Icons.external as React.ReactElement<{ size?: number }>, { size: 14 })}
            </span>
          </a>
        ))}
      </div>

      {f.notes && (
        <>
          <div className="divider" />
          <div className="tag-key">Notes</div>
          <div className="small" style={{
            marginTop: 8, color: 'var(--text-2)', lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
          }}>
            {f.notes}
          </div>
        </>
      )}

      <div className="divider" />

      <button className="btn ghost sm" style={{ color: 'var(--err)', justifyContent: 'center' }}>
        {Icons.trash} Archive filament
      </button>
    </div>
  );
}

// ---------- main screen ----------

export function FilamentsScreen() {
  const [type, setType] = useState('all');
  const [maker, setMaker] = useState('all');
  const [query, setQuery] = useState('');
  const [favOnly, setFavOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [, bumpVersion] = useState(0);

  const filaments = FILAMENTS;

  const toggleFavorite = (id: string) => {
    const f = filaments.find(x => x.id === id);
    if (f) {
      f.favorite = !f.favorite;
      bumpVersion(v => v + 1);
    }
  };

  const types = useMemo<[string, number][]>(() => {
    const counts: Record<string, number> = {};
    filaments.forEach(f => { counts[f.type] = (counts[f.type] ?? 0) + 1; });
    return (Object.entries(counts) as [string, number][]).sort((a, b) => b[1] - a[1]);
  }, [filaments]);

  const makers = useMemo(() => {
    const set = new Set(filaments.map(f => f.manufacturer));
    return Array.from(set).sort();
  }, [filaments]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return filaments.filter(f => {
      if (favOnly && !f.favorite) return false;
      if (type !== 'all' && f.type !== type) return false;
      if (maker !== 'all' && f.manufacturer !== maker) return false;
      if (!q) return true;
      const hay = [f.name, f.manufacturer, f.type, f.subtype, f.colorName, f.notes].join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [filaments, type, maker, query, favOnly]);

  const totals = {
    total:    filaments.length,
    types:    new Set(filaments.map(f => f.type)).size,
    makers:   makers.length,
    profiles: filaments.reduce((a, f) => a + (f.profiles?.length ?? 0), 0),
    favorites: filaments.filter(f => f.favorite).length,
  };

  const selectedFil = selected ? filaments.find(f => f.id === selected) : null;

  return (
    <div className="screen-grid" style={{ gridTemplateColumns: selectedFil ? '1fr 380px' : '1fr', gap: 18 }}>
      <div>
        {/* Summary strip */}
        <div className="row gap-3" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
          <FilSummary label="Spools tracked"   value={totals.total}     sub={`${totals.types} material types`} />
          <FilSummary label="Manufacturers"    value={totals.makers}    sub="across the library" />
          <FilSummary label="Profiles on file" value={totals.profiles}  sub="printer × nozzle combos" />
          <FilSummary label="Favorites"        value={totals.favorites} sub="pinned for quick claim" pillTone="accent" />
        </div>

        {/* Type filter chips */}
        <div className="row between" style={{ marginBottom: 12, flexWrap: 'wrap', gap: 12 }}>
          <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
            <FilChip active={type === 'all'} onClick={() => setType('all')}>
              All <span className="num muted" style={{ marginLeft: 4 }}>{filaments.length}</span>
            </FilChip>
            {types.map(([t, n]) => (
              <FilChip key={t} active={type === t} onClick={() => setType(t)}>
                {t} <span className="num muted" style={{ marginLeft: 4 }}>{n}</span>
              </FilChip>
            ))}
          </div>
          <div className="row gap-2">
            <FilChip active={favOnly} onClick={() => setFavOnly(v => !v)}>
              <Star filled={favOnly} /> Favorites
            </FilChip>
          </div>
        </div>

        {/* Search + manufacturer + add */}
        <div className="row gap-2" style={{ marginBottom: 14 }}>
          <div className="search" style={{
            flex: 1, display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 12px', background: 'var(--bg-2)',
            border: '1px solid var(--border-1)', borderRadius: 8,
          }}>
            {React.cloneElement(Icons.search as React.ReactElement<{ size?: number }>, { size: 14 })}
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search by name, color, brand, notes…"
              style={{ flex: 1, background: 'transparent', border: 'none', color: 'var(--text-1)', outline: 'none', fontSize: 13, fontFamily: 'var(--font-sans)' }}
            />
            {query && (
              <button className="btn ghost icon sm" onClick={() => setQuery('')} title="Clear">
                {Icons.x}
              </button>
            )}
          </div>
          <select
            value={maker}
            onChange={e => setMaker(e.target.value)}
            style={{
              padding: '8px 12px', background: 'var(--bg-2)',
              border: '1px solid var(--border-1)', borderRadius: 8,
              color: 'var(--text-1)', fontSize: 13, fontFamily: 'var(--font-sans)',
              minWidth: 160, cursor: 'pointer',
            }}>
            <option value="all">All manufacturers</option>
            {makers.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <button className="btn primary sm">{Icons.plus} Add filament</button>
        </div>

        {/* List column header */}
        <div className="row gap-3" style={{
          padding: '0 16px 8px',
          color: 'var(--text-4)',
          fontSize: 10.5,
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          fontWeight: 500,
        }}>
          <div style={{ width: 40, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>Filament</div>
          <div style={{ width: 110, flexShrink: 0 }}>Material</div>
          <div style={{ width: 130, flexShrink: 0 }}>Profiles</div>
        </div>

        {/* List */}
        <div className="col" style={{ gap: 8 }}>
          {filtered.map(fil => (
            <FilamentCard
              key={fil.id}
              filament={fil}
              selected={selected === fil.id}
              onClick={() => setSelected(fil.id === selected ? null : fil.id)}
              onToggleFavorite={() => toggleFavorite(fil.id)}
            />
          ))}
          {filtered.length === 0 && (
            <Empty
              title="No filaments match"
              sub="Try clearing filters or adjust your search."
              icon={Icons.spool}
            />
          )}
        </div>
      </div>

      {selectedFil && (
        <FilamentDetailPanel
          filament={selectedFil}
          onClose={() => setSelected(null)}
          onToggleFavorite={() => toggleFavorite(selectedFil.id)}
        />
      )}
    </div>
  );
}
