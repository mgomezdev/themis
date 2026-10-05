import { useCallback, useEffect, useState } from 'react';
import {
  fetchTabSchema, pluginPath, pluginRequest, type SchemaBlock, type SchemaField, type TabSchema,
} from '../api/plugins';
import { FieldRow, PageHeader, Toggle } from './settingsUi';

/** Renders a tab a plugin describes with a small JSON schema (`GET /api/v1/plugins/{id}/ui/{tab}`): `form` blocks load and
 *  save one JSON object; `table` blocks list rows, optionally with a create form and per-row actions. All requests go to the
 *  plugin's own API (`/api/v1/plugins/{id}/…`); Themis renders everything, so the plugin ships no frontend code. */

type Row = Record<string, unknown>;
const json = (method: string, body?: unknown): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

function fieldValue(f: SchemaField, raw: string | boolean | undefined): unknown {
  if (f.type === 'checkbox') return !!raw;
  if (raw === undefined || raw === '') return null;
  return f.type === 'number' ? Number(raw) : raw;
}

function FieldInput({ f, value, onChange }: { f: SchemaField; value: unknown; onChange: (v: string | boolean) => void }) {
  if (f.type === 'checkbox') return <Toggle checked={!!value} onChange={onChange} />;
  if (f.type === 'select') {
    return (
      <select className="select" aria-label={f.label} value={String(value ?? '')} onChange={e => onChange(e.target.value)}>
        <option value="">—</option>
        {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }
  if (f.type === 'textarea') {
    return <textarea className="input" aria-label={f.label} rows={3} value={String(value ?? '')} onChange={e => onChange(e.target.value)} />;
  }
  return <input className="input" aria-label={f.label} type={f.type === 'number' ? 'number' : 'text'} value={value == null ? '' : String(value)}
                onChange={e => onChange(e.target.value)} />;
}

function FormBlock({ pluginId, block }: { pluginId: string; block: Extract<SchemaBlock, { type: 'form' }> }) {
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    pluginRequest<Row>(pluginPath(pluginId, block.load))
      .then(r => { if (alive) { setValues(Object.fromEntries(block.fields.map(f => [f.key, (r[f.key] ?? (f.type === 'checkbox' ? false : '')) as string | boolean]))); setLoaded(true); } })
      .catch(e => { if (alive) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); setLoaded(true); } });
    return () => { alive = false; };
  }, [pluginId, block]);

  async function save() {
    setMsg(null);
    try {
      await pluginRequest(pluginPath(pluginId, block.save), json('PUT', Object.fromEntries(block.fields.map(f => [f.key, fieldValue(f, values[f.key])]))));
      setMsg({ ok: true, text: 'Saved' });
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
  }

  return (
    <section data-testid="schema-form" style={{ marginBottom: 24 }}>
      {block.title && <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>{block.title}</h3>}
      {block.description && <div className="muted small" style={{ marginBottom: 8 }}>{block.description}</div>}
      {!loaded ? <div className="muted small">Loading…</div> : (
        <>
          {block.fields.map(f => (
            <FieldRow key={f.key} label={`${f.label}${f.required ? ' *' : ''}`} hint={f.help}>
              <FieldInput f={f} value={values[f.key]} onChange={v => setValues(s => ({ ...s, [f.key]: v }))} />
            </FieldRow>
          ))}
          <div className="row gap-2" style={{ padding: '12px 0', alignItems: 'center' }}>
            <button className="btn primary sm" onClick={save}>Save</button>
            {msg && <span role={msg.ok ? 'status' : 'alert'} className="small" style={{ color: msg.ok ? 'var(--ok)' : 'var(--err)' }}>{msg.text}</span>}
          </div>
        </>
      )}
    </section>
  );
}

function TableBlock({ pluginId, block }: { pluginId: string; block: Extract<SchemaBlock, { type: 'table' }> }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});

  const load = useCallback(() => {
    pluginRequest<Row[] | { items: Row[] }>(pluginPath(pluginId, block.data))
      .then(r => { setRows(Array.isArray(r) ? r : r.items ?? []); setError(''); })
      .catch(e => { setRows([]); setError(e instanceof Error ? e.message : String(e)); });
  }, [pluginId, block.data]);
  useEffect(load, [load]);

  async function create() {
    if (!block.create) return;
    const body = Object.fromEntries(block.create.fields.map(f => [f.key, fieldValue(f, draft[f.key])]));
    try {
      await pluginRequest(pluginPath(pluginId, block.create.path), json('POST', body));
      setDraft({});
      load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  async function act(action: NonNullable<typeof block.row_actions>[number], row: Row) {
    if (action.confirm && !window.confirm(action.confirm)) return;
    try {
      await pluginRequest(pluginPath(pluginId, action.path, row), json(action.method, action.body));
      load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <section data-testid="schema-table" style={{ marginBottom: 24 }}>
      {block.title && <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>{block.title}</h3>}
      {block.description && <div className="muted small" style={{ marginBottom: 8 }}>{block.description}</div>}
      {error && <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: 8 }}>{error}</div>}
      {rows === null ? <div className="muted small">Loading…</div> : rows.length === 0 ? (
        <div className="muted small">{block.empty ?? 'Nothing here yet.'}</div>
      ) : (
        <table className="table" style={{ width: '100%' }}>
          <thead><tr>{block.columns.map(c => <th key={c.key} style={{ textAlign: 'left' }}>{c.label}</th>)}{block.row_actions && <th />}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={String(r.id ?? r.ref ?? i)}>
                {block.columns.map(c => <td key={c.key}>{r[c.key] == null ? '' : String(r[c.key])}</td>)}
                {block.row_actions && (
                  <td className="row gap-2">
                    {block.row_actions.map(a => <button key={a.label} className="btn ghost sm" onClick={() => act(a, r)}>{a.label}</button>)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {block.create && (
        <div className="row gap-2" style={{ marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          {block.create.fields.map(f => (
            <label key={f.key} className="col" style={{ gap: 4 }}>
              <span className="tiny muted">{f.label}{f.required ? ' *' : ''}</span>
              <FieldInput f={f} value={draft[f.key]} onChange={v => setDraft(d => ({ ...d, [f.key]: v }))} />
            </label>
          ))}
          <button className="btn sm" onClick={create}
                  disabled={block.create.fields.some(f => f.required && (draft[f.key] === undefined || draft[f.key] === ''))}>
            {block.create.label ?? 'Add'}
          </button>
        </div>
      )}
    </section>
  );
}

export function SchemaTab({ pluginId, tabId }: { pluginId: string; tabId: string }) {
  const [schema, setSchema] = useState<TabSchema | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    setSchema(null); setError('');
    fetchTabSchema(pluginId, tabId)
      .then(s => { if (alive) setSchema(s); })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [pluginId, tabId]);

  if (error) return <div role="alert" style={{ color: 'var(--err)' }}>{error}</div>;
  if (!schema) return <div className="muted small">Loading…</div>;
  return (
    <div data-testid="schema-tab">
      {schema.title && <PageHeader title={schema.title} />}
      {schema.blocks.map((b, i) => b.type === 'form'
        ? <FormBlock key={i} pluginId={pluginId} block={b} />
        : <TableBlock key={i} pluginId={pluginId} block={b} />)}
    </div>
  );
}
