import React, { useState } from 'react';
import { PRINTERS } from '../data/mock';
import { Icons } from '../components/icons';
import { StatusPill, SectionHeader } from '../components/ui';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ConnectionType = 'lan' | 'cloud' | 'octo';
type ConnStatus = 'idle' | 'testing' | 'success' | 'error';

interface WizardData {
  model: string;
  nickname: string;
  connection: ConnectionType;
  ip: string;
  accessCode: string;
  capabilities: string[];
}

// ---------------------------------------------------------------------------
// PrinterAddForm — 3-step wizard
// ---------------------------------------------------------------------------

const PRINTER_MODELS = [
  { name: 'Bambu Lab P1S',          vol: '256×256×256', chamber: false },
  { name: 'Bambu Lab X1 Carbon',    vol: '256×256×256', chamber: true  },
  { name: 'Elegoo Centauri Carbon', vol: '256×256×256', chamber: true  },
  { name: 'Snapmaker U1',           vol: '200×200×200', chamber: false },
  { name: 'Prusa MK4',              vol: '250×210×220', chamber: false },
  { name: 'Custom',                 vol: '—',           chamber: false },
] as const;

const ALL_CAPABILITIES = [
  'PLA', 'PETG', 'PLA-CF', 'ABS', 'ASA',
  'PA-CF', 'PC', 'TPU', 'Multi-color', 'Soluble support',
];

const CONNECTION_OPTIONS: { id: ConnectionType; label: string; sub: string }[] = [
  { id: 'lan',   label: 'LAN mode (manufacturer firmware)',   sub: 'Stream camera + control via local API.' },
  { id: 'cloud', label: 'Cloud account',                       sub: 'Sign in to vendor\'s cloud; OK for remote.' },
  { id: 'octo',  label: 'OctoPrint / Klipper / Moonraker',    sub: 'For custom firmware setups.' },
];

function PrinterAddForm({ onCancel }: { onCancel: () => void }) {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>({
    model: 'Bambu Lab P1S',
    nickname: '',
    connection: 'lan',
    ip: '192.168.1.',
    accessCode: '',
    capabilities: ['PLA', 'PETG'],
  });
  const [connStatus, setConnStatus] = useState<ConnStatus>('idle');

  function testConnection() {
    setConnStatus('testing');
    setTimeout(() => setConnStatus('success'), 1000);
  }

  function handleFinish() {
    console.log('Add printer:', data);
    onCancel();
  }

  const steps = [
    { n: 1, label: 'Model' },
    { n: 2, label: 'Connect' },
    { n: 3, label: 'Capabilities' },
  ] as const;

  return (
    <div className="col gap-4">
      {/* Breadcrumb */}
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>
          {Icons.chevL} Printers
        </button>
        <span className="muted small">/</span>
        <span className="small">Add printer</span>
      </div>

      <div style={{ maxWidth: 760 }}>
        {/* Step indicators */}
        <div className="row gap-3" style={{ marginBottom: 24 }}>
          {steps.map(s => (
            <div key={s.n} className="row gap-2" style={{ alignItems: 'center' }}>
              <div style={{
                width: 24, height: 24, borderRadius: 12,
                display: 'grid', placeItems: 'center',
                fontSize: 11, fontWeight: 600,
                background: step >= s.n ? 'var(--accent)' : 'var(--bg-3)',
                color: step >= s.n ? '#04101f' : 'var(--text-3)',
                border: step === s.n ? '2px solid var(--accent-hi)' : '1px solid var(--border-1)',
              }}>
                {step > s.n ? '✓' : s.n}
              </div>
              <span className="small" style={{ color: step >= s.n ? 'var(--text-1)' : 'var(--text-3)' }}>
                {s.label}
              </span>
              {s.n < 3 && (
                <div style={{ width: 40, height: 1, background: 'var(--border-1)', marginLeft: 8 }} />
              )}
            </div>
          ))}
        </div>

        {/* Step 1: Model selection */}
        {step === 1 && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Pick a printer model" sub="We'll suggest sensible defaults." />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
              {PRINTER_MODELS.map(m => {
                const active = data.model === m.name;
                return (
                  <button key={m.name}
                    onClick={() => setData({ ...data, model: m.name })}
                    className="card"
                    style={{
                      textAlign: 'left', padding: 14, cursor: 'pointer',
                      background: active ? 'var(--bg-3)' : 'var(--bg-1)',
                      borderColor: active ? 'var(--accent)' : 'var(--border-1)',
                    }}>
                    <div className="row between">
                      <div style={{ fontWeight: 500 }}>{m.name}</div>
                      {active && <div style={{ color: 'var(--accent-hi)' }}>{Icons.check}</div>}
                    </div>
                    <div className="tiny muted" style={{ marginTop: 4 }}>
                      {m.vol} mm · {m.chamber ? 'enclosed' : 'open'}
                    </div>
                  </button>
                );
              })}
            </div>
            <div style={{ marginTop: 20 }}>
              <label className="label">Nickname</label>
              <input className="input" value={data.nickname}
                placeholder="e.g. Atlas, Forge, Iris"
                onChange={e => setData({ ...data, nickname: e.target.value })} />
              <div className="tiny muted" style={{ marginTop: 6 }}>
                Shown in queue and on tiles. Real model name stays in metadata.
              </div>
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn ghost" onClick={onCancel}>Cancel</button>
              <button className="btn primary" onClick={() => setStep(2)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Connection */}
        {step === 2 && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="How should we talk to this printer?" />
            <div className="col gap-3">
              {CONNECTION_OPTIONS.map(opt => {
                const active = data.connection === opt.id;
                return (
                  <button key={opt.id}
                    onClick={() => setData({ ...data, connection: opt.id })}
                    className="card"
                    style={{
                      textAlign: 'left', padding: 14, cursor: 'pointer',
                      background: active ? 'var(--bg-3)' : 'var(--bg-1)',
                      borderColor: active ? 'var(--accent)' : 'var(--border-1)',
                    }}>
                    <div className="row between">
                      <div>
                        <div style={{ fontWeight: 500 }}>{opt.label}</div>
                        <div className="tiny muted" style={{ marginTop: 2 }}>{opt.sub}</div>
                      </div>
                      <div style={{
                        width: 16, height: 16, borderRadius: 8,
                        border: `2px solid ${active ? 'var(--accent)' : 'var(--border-2)'}`,
                        background: active ? 'var(--accent)' : 'transparent',
                        boxShadow: active ? 'inset 0 0 0 3px var(--bg-3)' : 'none',
                        flexShrink: 0,
                      }} />
                    </div>
                  </button>
                );
              })}
            </div>

            {data.connection === 'lan' && (
              <div style={{ marginTop: 20 }}>
                <label className="label">IP address</label>
                <input className="input mono" value={data.ip}
                  onChange={e => setData({ ...data, ip: e.target.value })} />
                <label className="label" style={{ marginTop: 12 }}>Access code</label>
                <input className="input mono" placeholder="••••••••"
                  value={data.accessCode}
                  onChange={e => setData({ ...data, accessCode: e.target.value })} />
              </div>
            )}

            {connStatus === 'success' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--ok)', fontSize: 13 }}>
                {Icons.check} Connection successful
              </div>
            )}
            {connStatus === 'error' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--err)', fontSize: 13 }}>
                {Icons.alert} Could not connect
              </div>
            )}

            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(1)}>{Icons.chevL} Back</button>
              <button className="btn"
                onClick={testConnection}
                disabled={connStatus === 'testing'}>
                {connStatus === 'testing' ? 'Testing…' : 'Test connection'}
              </button>
              <button className="btn primary" onClick={() => setStep(3)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Capabilities */}
        {step === 3 && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader
              title="Queue eligibility"
              sub="Which materials this printer can claim from the queue."
            />
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 8,
              marginTop: 12,
            }}>
              {ALL_CAPABILITIES.map(m => {
                const on = data.capabilities.includes(m);
                return (
                  <button key={m}
                    onClick={() => {
                      const next = on
                        ? data.capabilities.filter(x => x !== m)
                        : [...data.capabilities, m];
                      setData({ ...data, capabilities: next });
                    }}
                    className="row gap-2"
                    style={{
                      padding: '10px 12px', borderRadius: 8,
                      background: on ? 'rgba(59,130,246,0.12)' : 'var(--bg-1)',
                      border: `1px solid ${on ? 'rgba(59,130,246,0.4)' : 'var(--border-1)'}`,
                      color: on ? 'var(--accent-hi)' : 'var(--text-2)',
                      cursor: 'pointer', textAlign: 'left',
                    }}>
                    <div style={{
                      width: 14, height: 14, borderRadius: 3,
                      background: on ? 'var(--accent)' : 'transparent',
                      border: `1.5px solid ${on ? 'var(--accent)' : 'var(--border-2)'}`,
                      display: 'grid', placeItems: 'center', color: '#04101f', flexShrink: 0,
                    }}>
                      {on && (
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="3">
                          <path d="M20 6 9 17l-5-5" />
                        </svg>
                      )}
                    </div>
                    <span className="small">{m}</span>
                  </button>
                );
              })}
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(2)}>{Icons.chevL} Back</button>
              <button className="btn primary" onClick={handleFinish}>
                {Icons.check} Finish
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrintersScreen — main export
// ---------------------------------------------------------------------------

export function PrintersScreen() {
  const [adding, setAdding] = useState(false);

  if (adding) {
    return <PrinterAddForm onCancel={() => setAdding(false)} />;
  }

  return (
    <div className="col gap-4">
      <SectionHeader
        title="Printers"
        sub="3 connected · 0 offline"
        actions={
          <button className="btn primary sm" onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        }
      />

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Printer</th>
              <th>Model</th>
              <th>Build volume</th>
              <th>Capabilities</th>
              <th>Status</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {PRINTERS.map(p => (
              <tr key={p.id}>
                <td>
                  <div className="row gap-3">
                    <div style={{
                      width: 36, height: 36, borderRadius: 8,
                      background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
                      border: '1px solid var(--border-1)',
                      display: 'grid', placeItems: 'center',
                    }}>
                      <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
                    </div>
                    <div className="col">
                      <div style={{ fontWeight: 500 }}>{p.nickname}</div>
                      <div className="mono tiny muted">{p.id}</div>
                    </div>
                  </div>
                </td>
                <td><div className="small">{p.name}</div></td>
                <td className="num small">{p.buildVolume} mm</td>
                <td>
                  <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                    {p.capabilities.slice(0, 4).map(c => (
                      <span key={c} className="elig on">{c}</span>
                    ))}
                    {p.capabilities.length > 4 && (
                      <span className="elig">+{p.capabilities.length - 4}</span>
                    )}
                  </div>
                </td>
                <td><StatusPill status={p.status} /></td>
                <td style={{ textAlign: 'right' }}>
                  <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                    <button className="btn ghost sm" onClick={e => e.stopPropagation()}>Edit</button>
                    <button className="btn icon ghost sm">{Icons.more}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
