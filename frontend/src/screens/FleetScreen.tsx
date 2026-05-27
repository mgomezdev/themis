import React, { useState, useEffect } from 'react';
import { JOBS } from '../data/mock';
import { useFleetData } from '../api/fleet';
import { fmtTime } from '../data/helpers';
import { StatusPill, Progress, VideoTile, Swatch, Kv } from '../components/ui';
import { Icons } from '../components/icons';
import type { Printer, Job } from '../data/types';
import { pausePrinter, resumePrinter, stopPrinter, fetchPrinterTypes, type PrinterType } from '../api/printers';
import { PrinterAddForm } from './PrintersScreen';

type Layout = 'cards' | 'rows';

function partsFromJob(job: Job) {
  return job.parts.map(p => ({ name: p.partId, orderId: p.orderId, material: job.material, qty: p.qty }));
}

// ── Telem row ────────────────────────────────────────────────────────────────
function Telem({ label, value, target, tone }: { label: string; value: string; target: string; tone?: string | null }) {
  return (
    <div className="row between">
      <div className="small muted">{label}</div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: tone === 'warn' ? 'var(--warn)' : 'var(--text-1)' }}>
          {value}
        </span>
        <span className="num tiny muted">/ {target}</span>
      </div>
    </div>
  );
}

// ── Fan icon (spins proportional to duty cycle) ───────────────────────────────
function FanIcon({ pct }: { pct: number }) {
  const running = pct > 0;
  const period = running ? Math.max(0.16, 1.8 - (pct / 100) * 1.6) : 0;
  return (
    <svg width="16" height="16" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round"
         style={{
           color: running ? 'var(--accent-hi)' : 'var(--text-4)',
           animation: running ? `spin ${period.toFixed(2)}s linear infinite` : 'none',
           transformOrigin: 'center',
           flexShrink: 0,
         }}>
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <path d="M12 10.5 C 12 6.5, 14.5 4, 18.5 4 C 18.5 8, 16 10.5, 12 10.5 Z" />
      <path d="M13.5 12 C 17.5 12, 20 14.5, 20 18.5 C 16 18.5, 13.5 16, 13.5 12 Z" />
      <path d="M12 13.5 C 12 17.5, 9.5 20, 5.5 20 C 5.5 16, 8 13.5, 12 13.5 Z" />
      <path d="M10.5 12 C 6.5 12, 4 9.5, 4 5.5 C 8 5.5, 10.5 8, 10.5 12 Z" />
    </svg>
  );
}

function FanTelem({ label, pct, maxRpm = 7000 }: { label: string; pct: number; maxRpm?: number }) {
  const running = pct > 0;
  const rpm = Math.round((pct / 100) * maxRpm);
  return (
    <div className="row between" style={{ alignItems: 'center' }}>
      <div className="row gap-2" style={{ alignItems: 'center' }}>
        <FanIcon pct={pct} />
        <span className="small muted">{label}</span>
      </div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: running ? 'var(--text-1)' : 'var(--text-3)' }}>
          {pct}%
        </span>
        <span className="num tiny muted">{rpm} rpm</span>
      </div>
    </div>
  );
}

// ── Capability checkbox grid (inside EditPrinterModal) ───────────────────────
const ALL_CAPS = ['PLA','PETG','PLA-CF','PETG-CF','ABS','ASA','PA-CF','PC','TPU','Multi-color','Soluble support'];

function CapabilityGrid({ draft, setDraft }: { draft: string[]; setDraft: (v: string[]) => void }) {
  const toggle = (m: string) => setDraft(draft.includes(m) ? draft.filter(x => x !== m) : [...draft, m]);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
      {ALL_CAPS.map(m => {
        const on = draft.includes(m);
        return (
          <button key={m} onClick={() => toggle(m)} className="row gap-2"
                  style={{
                    padding: '8px 12px', borderRadius: 6,
                    background: on ? 'rgba(59,130,246,0.12)' : 'var(--bg-1)',
                    border: `1px solid ${on ? 'rgba(59,130,246,0.4)' : 'var(--border-1)'}`,
                    color: on ? 'var(--accent-hi)' : 'var(--text-2)',
                    cursor: 'pointer', textAlign: 'left', alignItems: 'center',
                  }}>
            <div style={{
              width: 14, height: 14, borderRadius: 3, flexShrink: 0,
              background: on ? 'var(--accent)' : 'transparent',
              border: `1.5px solid ${on ? 'var(--accent)' : 'var(--border-2)'}`,
              display: 'grid', placeItems: 'center', color: '#04101f',
            }}>
              {on && <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4"><path d="M20 6 9 17l-5-5"/></svg>}
            </div>
            <span className="small">{m}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Edit printer modal ───────────────────────────────────────────────────────
type ConnectionType = 'lan' | 'cloud' | 'octo';
const CONNECTION_OPTS: { id: ConnectionType; label: string }[] = [
  { id: 'lan',   label: 'LAN mode (manufacturer firmware)' },
  { id: 'cloud', label: 'Cloud account' },
  { id: 'octo',  label: 'OctoPrint / Klipper / Moonraker' },
];

function EditPrinterModal({ printer: p, nickname, caps, onChangeNickname, onChangeCaps, onClose }: {
  printer: Printer;
  nickname: string;
  caps: string[];
  onChangeNickname: (v: string) => void;
  onChangeCaps: (v: string[]) => void;
  onClose: () => void;
}) {
  const [draftName, setDraftName] = useState(nickname);
  const [draftCaps, setDraftCaps] = useState(caps);
  const [connection, setConnection] = useState<ConnectionType>('lan');
  const [ip, setIp] = useState('');

  const save = () => { onChangeNickname(draftName); onChangeCaps(draftCaps); onClose(); };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0,
      background: 'rgba(2,6,16,0.65)', backdropFilter: 'blur(4px)',
      zIndex: 100, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: 'min(640px, 100%)', maxHeight: '90vh', overflowY: 'auto',
        padding: 0, borderColor: 'var(--border-3)',
        boxShadow: '0 20px 60px -20px rgba(0,0,0,0.7), 0 0 0 1px var(--accent-glow)',
      }}>
        <div className="row between" style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border-1)',
          alignItems: 'center', background: 'var(--bg-3)',
        }}>
          <div className="row gap-3" style={{ alignItems: 'center' }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
              border: '1px solid var(--border-1)', display: 'grid', placeItems: 'center',
            }}>
              <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
            </div>
            <div className="col">
              <div className="tag-key">Edit printer</div>
              <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>{p.name}</div>
            </div>
          </div>
          <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
        </div>

        <div className="col gap-5" style={{ padding: 20 }}>
          <div className="col gap-2">
            <div className="tag-key">Identity</div>
            <label className="label">Nickname</label>
            <input className="input" value={draftName} onChange={e => setDraftName(e.target.value)} />
            <div className="row gap-4" style={{ marginTop: 4 }}>
              {([
                { label: 'Model', value: p.name },
                { label: 'Build volume', value: `${p.buildVolume} mm` },
                { label: 'Chamber', value: p.chamber ? 'enclosed' : 'open frame' },
                { label: 'Printer ID', value: p.id },
              ] as const).map(kv => (
                <div key={kv.label} className="col">
                  <span className="tag-key" style={{ fontSize: 9.5 }}>{kv.label}</span>
                  <span className="small mono" style={{ marginTop: 2 }}>{kv.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="col gap-3">
            <div className="tag-key">Connection</div>
            <div className="col gap-2">
              {CONNECTION_OPTS.map(opt => {
                const on = connection === opt.id;
                return (
                  <button key={opt.id} onClick={() => setConnection(opt.id)} className="row gap-3"
                          style={{
                            padding: '10px 12px', textAlign: 'left',
                            background: on ? 'var(--bg-3)' : 'var(--bg-1)',
                            border: `1px solid ${on ? 'var(--accent)' : 'var(--border-1)'}`,
                            borderRadius: 8, cursor: 'pointer', alignItems: 'center',
                          }}>
                    <div style={{
                      width: 14, height: 14, borderRadius: 7, flexShrink: 0,
                      border: `2px solid ${on ? 'var(--accent)' : 'var(--border-2)'}`,
                      background: on ? 'var(--accent)' : 'transparent',
                      boxShadow: on ? 'inset 0 0 0 2px var(--bg-3)' : 'none',
                    }} />
                    <span className="small" style={{ color: on ? 'var(--text-1)' : 'var(--text-2)' }}>{opt.label}</span>
                  </button>
                );
              })}
            </div>
            {connection === 'lan' && (
              <div>
                <label className="label">IP address</label>
                <input className="input mono" value={ip} onChange={e => setIp(e.target.value)} placeholder="192.168.1.x" />
              </div>
            )}
          </div>

          <div className="col gap-3">
            <div className="row between" style={{ alignItems: 'baseline' }}>
              <div className="tag-key">Queue eligibility</div>
              <span className="tiny muted">{draftCaps.length} selected</span>
            </div>
            <CapabilityGrid draft={draftCaps} setDraft={setDraftCaps} />
          </div>
        </div>

        <div className="row between" style={{
          padding: '14px 20px', borderTop: '1px solid var(--border-1)',
          background: 'var(--bg-3)', alignItems: 'center',
        }}>
          <button className="btn ghost sm" style={{ color: 'var(--err)' }}>
            {Icons.trash} Remove printer
          </button>
          <div className="row gap-2">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={save}>{Icons.check} Save changes</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Filament picker (UI stub — not wired to filament API yet) ────────────────
function FilamentPicker({ onClose }: { onClose: () => void }) {
  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-1)' }}>
      <div className="tiny muted" style={{ padding: '20px 8px', textAlign: 'center' }}>
        Filament library not connected yet.
      </div>
      <div className="row between" style={{ marginTop: 10 }}>
        <button className="btn ghost sm" onClick={onClose}>{Icons.x} Unload spool</button>
        <button className="btn ghost sm" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

// ── PrinterExpandedCard ───────────────────────────────────────────────────────
function PrinterExpandedCard({ printer: p, onCollapse }: { printer: Printer; onCollapse: () => void }) {
  const isPrinting = p.status === 'printing';
  const isPaused = p.status === 'paused';
  const [nickname, setNickname] = useState(p.nickname);
  const [editingName, setEditingName] = useState(false);
  const [caps, setCaps] = useState(p.capabilities);
  const [editingPrinter, setEditingPrinter] = useState(false);
  const [pickingFilament, setPickingFilament] = useState(false);

  const job: Job | undefined = p.currentJobId ? JOBS.find(j => j.id === p.currentJobId) : undefined;

  return (
    <>
      <div className="card" style={{
        padding: 0, overflow: 'hidden',
        borderColor: 'var(--border-3)',
        boxShadow: '0 0 0 1px var(--accent-glow), 0 18px 40px -20px rgba(0,0,0,0.6)',
      }}>
        {/* Header */}
        <div className="row between" style={{
          padding: '14px 18px', background: 'var(--bg-3)',
          borderBottom: '1px solid var(--border-1)', gap: 16, alignItems: 'center',
        }}>
          <div className="col" style={{ minWidth: 0, flex: 1 }}>
            <div className="row gap-2" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
              {editingName ? (
                <input autoFocus className="input" value={nickname}
                       onChange={e => setNickname(e.target.value)}
                       onBlur={() => setEditingName(false)}
                       onKeyDown={e => { if (e.key === 'Enter' || e.key === 'Escape') setEditingName(false); }}
                       style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', padding: '2px 8px', width: 220, background: 'var(--bg-1)' }} />
              ) : (
                <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em' }}>{nickname}</div>
              )}
              <button className="btn ghost icon sm" title="Rename" onClick={() => setEditingName(v => !v)}>
                <svg className="ico" width="14" height="14" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9"/>
                  <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
                </svg>
              </button>
              <div className="muted small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>{p.name}</div>
              <span className="mono tiny muted" style={{ flexShrink: 0 }}>· {p.id}</span>
            </div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <span className="num">{p.buildVolume}</span> mm · {p.chamber ? 'enclosed' : 'open frame'} · capable: {caps.join(' · ')}
            </div>
          </div>
          <div className="row gap-2" style={{ flexShrink: 0 }}>
            <StatusPill status={p.status} />
            <button className="btn sm">{Icons.camera} Snapshot</button>
            <button className="btn icon sm" title="Edit printer" onClick={() => setEditingPrinter(true)}>
              {Icons.wrench}
            </button>
            <button className="btn ghost icon sm" title="Collapse" onClick={onCollapse}>{Icons.x}</button>
          </div>
        </div>

        {/* Body */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.6fr) minmax(300px, 1fr)', gap: 18, padding: 18 }}>
          {/* LEFT */}
          <div className="col gap-4" style={{ minWidth: 0 }}>
            <VideoTile live={p.status !== 'offline'} time={isPrinting ? p.timeElapsed : undefined} printerId={p.id} />

            <div className="row gap-6" style={{ flexWrap: 'wrap' }}>
              <Kv k="Progress" v={<span className="num" style={{ fontSize: 22, fontWeight: 600 }}>{p.progress}%</span>} />
              <Kv k="Time left" v={
                <span className="num" style={{ fontSize: 22, fontWeight: 600, color: isPrinting ? 'var(--accent-hi)' : 'var(--text-3)' }}>
                  {isPrinting ? fmtTime(p.timeRemaining) : '—'}
                </span>
              } />
              <Kv k="Elapsed" v={<span className="num" style={{ fontSize: 22 }}>{isPrinting ? fmtTime(p.timeElapsed) : '—'}</span>} />
              {p.layer && (
                <Kv k="Layer" v={
                  <span className="num" style={{ fontSize: 22 }}>
                    {p.layer.now}<span className="muted" style={{ fontSize: 14 }}> / {p.layer.total}</span>
                  </span>
                } />
              )}
            </div>
            {isPrinting && <Progress value={p.progress} large />}

            <div className="row gap-2" style={{ marginTop: 2 }}>
              {isPrinting && (
                <>
                  <button className="btn" onClick={() => pausePrinter(p.id).catch(console.error)}>{Icons.pause} Pause</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {isPaused && (
                <>
                  <button className="btn" onClick={() => resumePrinter(p.id).catch(console.error)}>{Icons.play} Resume</button>
                  <button className="btn" onClick={() => stopPrinter(p.id).catch(console.error)}>{Icons.stop} Stop</button>
                </>
              )}
              {!isPrinting && !isPaused && (
                <button className="btn primary">{Icons.play} Claim next from queue</button>
              )}
            </div>

            {job && (
              <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
                <div className="row between" style={{ marginBottom: 10, alignItems: 'center' }}>
                  <div className="col">
                    <span className="tag-key">Current job</span>
                    <div className="row gap-2" style={{ marginTop: 2 }}>
                      <span className="mono tiny muted">{job.id}</span>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{job.plateName}</span>
                    </div>
                  </div>
                  <button className="btn ghost sm">{Icons.arrowR} Open job</button>
                </div>
                <div className="col gap-2">
                  {partsFromJob(job).map((part, i) => (
                    <div key={i} className="row between" style={{
                      padding: '8px 12px', background: 'var(--bg-2)',
                      borderRadius: 8, border: '1px solid var(--border-1)',
                    }}>
                      <div className="col" style={{ minWidth: 0 }}>
                        <div className="small" style={{ fontWeight: 500 }}>{part.name}</div>
                        <div className="tiny muted">{part.orderId} · {part.material}</div>
                      </div>
                      <div className="num small" style={{ flexShrink: 0 }}>×{part.qty}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* RIGHT */}
          <div className="col gap-4">
            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="row between" style={{ alignItems: 'center' }}>
                <span className="tag-key">Loaded filament</span>
                <button className="btn ghost sm" onClick={() => setPickingFilament(v => !v)}>
                  {pickingFilament ? 'Cancel' : 'Change'}
                </button>
              </div>
              <div className="row gap-3" style={{ marginTop: 10 }}>
                <Swatch color={p.material.color} large />
                <div className="col" style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {p.material.name}
                  </div>
                  <div className="tiny muted">{p.material.type}</div>
                </div>
              </div>
              {pickingFilament && <FilamentPicker onClose={() => setPickingFilament(false)} />}
            </div>

            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="tag-key" style={{ marginBottom: 10 }}>Temperatures</div>
              <div className="col gap-3">
                <Telem label="Nozzle" value={`${p.nozzleTemp}°C`} target={isPrinting ? '220°C' : '—'} tone={isPrinting ? 'warn' : null} />
                <Telem label="Bed" value={`${p.bedTemp}°C`} target={isPrinting ? '60°C' : '—'} />
                {p.chamberTemp != null && (
                  <Telem label="Chamber" value={`${p.chamberTemp}°C`} target={p.chamber ? '60°C' : '—'} />
                )}
              </div>

              <div className="tag-key" style={{ marginTop: 16, marginBottom: 10 }}>Fans</div>
              <div className="col gap-3">
                <FanTelem label="Model" pct={p.fanModel} maxRpm={7200} />
                <FanTelem label="Auxiliary" pct={p.fanAux} maxRpm={5400} />
                <FanTelem label="Box" pct={p.fanBox} maxRpm={5400} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {editingPrinter && (
        <EditPrinterModal
          printer={p} nickname={nickname} caps={caps}
          onChangeNickname={setNickname} onChangeCaps={setCaps}
          onClose={() => setEditingPrinter(false)}
        />
      )}
    </>
  );
}

// ── PrinterTile ───────────────────────────────────────────────────────────────
function PrinterTile({ printer: p, onClick }: { printer: Printer; onClick: () => void }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{ cursor: 'pointer', padding: 0, overflow: 'hidden', transition: 'border-color 120ms ease' }}>
      <div className="row between" style={{ padding: '12px 14px 8px' }}>
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
        <StatusPill status={p.status} />
      </div>
      <div style={{ padding: '0 14px' }}>
        <VideoTile live={isPrinting} />
      </div>
      <div className="row between" style={{ padding: '12px 14px 10px' }}>
        <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
          <Swatch color={p.material.color} />
          <div className="col" style={{ minWidth: 0 }}>
            <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
            <div className="tiny muted">{p.material.type}</div>
          </div>
        </div>
        {isPrinting ? (
          <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap' }}>
            <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
              {fmtTime(p.timeRemaining)}
            </div>
            <div className="tiny muted">remaining</div>
          </div>
        ) : (
          <div className="tiny muted" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{p.note ?? '—'}</div>
        )}
      </div>
      {isPrinting && (
        <div style={{ padding: '0 14px 14px' }}>
          <Progress value={p.progress} />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">{p.currentJobId}</span>
            <span className="num tiny muted">{p.progress}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── PrinterRow (rows layout — no video feed) ──────────────────────────────────
function PrinterRow({ printer: p, expanded, onClick }: { printer: Printer; expanded: boolean; onClick: () => void }) {
  const isPrinting = p.status === 'printing';
  return (
    <div className="card" onClick={onClick} style={{
      cursor: 'pointer', padding: '12px 14px',
      display: 'grid',
      gridTemplateColumns: 'auto auto 1.4fr auto 1.6fr 1fr auto',
      alignItems: 'center', gap: 16,
      borderColor: expanded ? 'var(--accent)' : undefined,
      background: expanded ? 'var(--bg-3)' : undefined,
    }}>
      <span style={{ display: 'inline-flex', color: 'var(--text-3)', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease' }}>
        {Icons.chevR}
      </span>
      <div style={{
        width: 36, height: 36, borderRadius: 6,
        background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
        border: '1px solid var(--border-1)', display: 'grid', placeItems: 'center',
      }}>
        <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
      </div>
      <div className="col" style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{p.nickname}</div>
        <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
      </div>
      <StatusPill status={p.status} />
      <div className="row gap-2" style={{ alignItems: 'center', minWidth: 0 }}>
        <Swatch color={p.material.color} />
        <div className="col" style={{ minWidth: 0 }}>
          <div className="small" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.material.name}</div>
          <div className="tiny muted">{p.material.type}</div>
        </div>
      </div>
      <div className="col" style={{ minWidth: 0 }}>
        {isPrinting ? (
          <>
            <Progress value={p.progress} />
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="mono tiny muted">{p.currentJobId}</span>
              <span className="num tiny muted">{p.progress}%</span>
            </div>
          </>
        ) : (
          <span className="tiny muted">{p.note ?? '—'}</span>
        )}
      </div>
      {isPrinting ? (
        <div className="col" style={{ alignItems: 'flex-end', whiteSpace: 'nowrap', minWidth: 80 }}>
          <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text-1)' }}>
            {fmtTime(p.timeRemaining)}
          </div>
          <div className="tiny muted">remaining</div>
        </div>
      ) : (
        <div className="tiny muted" style={{ minWidth: 80, textAlign: 'right' }}>—</div>
      )}
    </div>
  );
}

// ── AddPrinterCard ────────────────────────────────────────────────────────────
function AddPrinterCard({ onClick, variant }: { onClick: () => void; variant: 'card' | 'row' }) {
  const hoverOn = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--accent)';
    e.currentTarget.style.color = 'var(--accent-hi)';
    e.currentTarget.style.background = 'var(--accent-glow)';
  };
  const hoverOff = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.borderColor = 'var(--border-2)';
    e.currentTarget.style.color = 'var(--text-3)';
    e.currentTarget.style.background = 'transparent';
  };

  if (variant === 'row') {
    return (
      <button onClick={onClick} className="row gap-3"
              style={{ padding: '14px 18px', borderRadius: 10, width: '100%',
                       border: '1.5px dashed var(--border-2)', background: 'transparent',
                       color: 'var(--text-3)', cursor: 'pointer', alignItems: 'center',
                       justifyContent: 'center', transition: 'all 140ms ease' }}
              onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
        {Icons.plus}
        <span className="small" style={{ fontWeight: 500 }}>Add printer</span>
      </button>
    );
  }
  return (
    <button onClick={onClick} className="col"
            style={{ alignItems: 'center', justifyContent: 'center', gap: 8,
                     minHeight: 280, borderRadius: 10, padding: 14,
                     border: '1.5px dashed var(--border-2)', background: 'transparent',
                     color: 'var(--text-3)', cursor: 'pointer', transition: 'all 140ms ease' }}
            onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
      <div style={{ width: 44, height: 44, borderRadius: 12, display: 'grid', placeItems: 'center',
                    background: 'var(--bg-3)', border: '1px solid var(--border-1)' }}>
        {Icons.plus}
      </div>
      <div className="small" style={{ fontWeight: 500 }}>Add printer</div>
      <div className="tiny muted" style={{ textAlign: 'center', maxWidth: 220 }}>
        Connect a Bambu, Elegoo, Snapmaker, Prusa or OctoPrint host.
      </div>
    </button>
  );
}

// ── Layout toggle (Cards / Rows) ──────────────────────────────────────────────
function LayoutToggle({ value, onChange }: { value: Layout; onChange: (v: Layout) => void }) {
  const opts: { id: Layout; label: string; icon: React.ReactElement }[] = [
    { id: 'cards', label: 'Cards', icon: Icons.fleet },
    { id: 'rows',  label: 'Rows',  icon: Icons.layers },
  ];
  return (
    <div className="row" style={{ gap: 0, padding: 2, borderRadius: 7, background: 'var(--bg-2)', border: '1px solid var(--border-1)' }}>
      {opts.map(o => {
        const on = value === o.id;
        return (
          <button key={o.id} onClick={() => onChange(o.id)} className="row gap-2" title={o.label}
                  style={{
                    padding: '4px 10px', borderRadius: 5, border: 'none',
                    background: on ? 'var(--bg-4)' : 'transparent',
                    color: on ? 'var(--text-1)' : 'var(--text-3)',
                    fontSize: 12, fontWeight: 500, cursor: 'pointer', alignItems: 'center',
                    boxShadow: on ? '0 1px 0 rgba(0,0,0,0.25), 0 0 0 1px var(--border-2)' : 'none',
                  }}>
            {React.cloneElement(o.icon, { size: 13 } as React.SVGProps<SVGSVGElement>)}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── FleetGrid (cards layout) ──────────────────────────────────────────────────
function FleetGrid({ printers, expandedId, onToggle, onAdd }: {
  printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id} style={{ gridColumn: expanded ? '1 / -1' : 'auto' }}>
            {expanded
              ? <PrinterExpandedCard printer={p} onCollapse={() => onToggle(p.id)} />
              : <PrinterTile printer={p} onClick={() => onToggle(p.id)} />}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="card" />
    </div>
  );
}

// ── FleetRows (rows layout) ───────────────────────────────────────────────────
function FleetRows({ printers, expandedId, onToggle, onAdd }: {
  printers: Printer[]; expandedId: string | null; onToggle: (id: string) => void; onAdd: () => void;
}) {
  return (
    <div className="col gap-2">
      {printers.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id}>
            <PrinterRow printer={p} expanded={expanded} onClick={() => onToggle(p.id)} />
            {expanded && (
              <div style={{ marginTop: 8 }}>
                <PrinterExpandedCard printer={p} onCollapse={() => onToggle(p.id)} />
              </div>
            )}
          </div>
        );
      })}
      <AddPrinterCard onClick={onAdd} variant="row" />
    </div>
  );
}

// ── FleetScreen ───────────────────────────────────────────────────────────────
export function FleetScreen() {
  const [printers, refetchFleet] = useFleetData();
  const [layout, setLayout] = useState<Layout>('cards');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [printerTypes, setPrinterTypes] = useState<PrinterType[]>([]);

  useEffect(() => {
    fetchPrinterTypes().then(setPrinterTypes).catch(console.error);
  }, []);

  const toggle = (id: string) => setExpandedId(expandedId === id ? null : id);
  const printingCount = printers.filter(p => p.status === 'printing').length;
  const idleCount = printers.filter(p => p.status === 'idle').length;

  if (adding) {
    return (
      <PrinterAddForm
        types={printerTypes}
        backLabel="Fleet"
        onCancel={() => setAdding(false)}
        onCreated={() => { setAdding(false); refetchFleet(); }}
      />
    );
  }

  return (
    <div className="col gap-5">
      <div className="row between">
        <div>
          <div className="tag-key" style={{ marginBottom: 2 }}>Workshop</div>
          <div className="row gap-3" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              {printers.length} printers online
            </div>
            <div className="muted small">{printingCount} printing · {idleCount} idle</div>
          </div>
        </div>
        <div className="row gap-2" style={{ alignItems: 'center' }}>
          <LayoutToggle value={layout} onChange={setLayout} />
          <button className="btn sm" style={{ whiteSpace: 'nowrap' }}>{Icons.refresh} Sync now</button>
          <button className="btn primary sm" style={{ whiteSpace: 'nowrap' }} onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        </div>
      </div>

      {layout === 'cards' && (
        <FleetGrid printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} />
      )}
      {layout === 'rows' && (
        <FleetRows printers={printers} expandedId={expandedId} onToggle={toggle} onAdd={() => setAdding(true)} />
      )}
    </div>
  );
}
