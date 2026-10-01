import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  acknowledgeAlarm, acknowledgeAll, getAlarmSettings, listAlarms, saveAlarmSettings, SEVERITIES, useAlarmFeed,
  type AlarmStatus, type PrinterAlarm, type Severity,
} from '../api/alarms';
import { Icons } from '../components/icons';
import { SEVERITY_COLOR } from '../lib/severity';

export function SeverityPill({ severity }: { severity: Severity }) {
  return (
    <span className="tiny num" data-testid={`severity-${severity}`}
          style={{ color: SEVERITY_COLOR[severity], border: `1px solid ${SEVERITY_COLOR[severity]}`, borderRadius: 999,
                   padding: '1px 8px', textTransform: 'uppercase', fontWeight: severity === 'fatal' ? 700 : 500 }}>
      {severity}
    </span>
  );
}

const STATUS_LABEL: Record<AlarmStatus, string> = { unacknowledged: 'Needs attention', active: 'Active', all: 'History' };

export function AlarmsScreen() {
  const [params, setParams] = useSearchParams();
  const printerParam = params.get('printer');
  const printerId = printerParam ? Number(printerParam) : undefined;
  const [status, setStatus] = useState<AlarmStatus>('unacknowledged');
  const [alarms, refetch] = useAlarmFeed<PrinterAlarm[]>(() => listAlarms(status, printerId), []);
  const [minSeverity, setMinSeverity] = useState<Severity>('warning');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { refetch(); }, [status, printerId]);            // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { getAlarmSettings().then(s => setMinSeverity(s.min_severity)).catch(() => {}); }, []);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); refetch(); return true; } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
  }

  return (
    <div className="page" data-testid="alarms-screen">
      <div className="row gap-3" style={{ alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        {(Object.keys(STATUS_LABEL) as AlarmStatus[]).map(s => (
          <button key={s} className={`btn sm${status === s ? ' primary' : ''}`} aria-pressed={status === s}
                  onClick={() => setStatus(s)}>{STATUS_LABEL[s]}</button>
        ))}
        {printerId != null && (
          <button className="btn ghost sm" onClick={() => setParams({})}>Showing one printer · show all {Icons.x}</button>
        )}
        <span style={{ flex: 1 }} />
        <label className="small muted" htmlFor="alarm-min-severity">Send webhook / notifications for</label>
        <select id="alarm-min-severity" className="input" style={{ width: 'auto' }} value={minSeverity}
                onChange={e => {
                  const v = e.target.value as Severity, prev = minSeverity;
                  setMinSeverity(v);
                  void run(() => saveAlarmSettings(v)).then(ok => { if (!ok) setMinSeverity(prev); });   // failed save → show what is really stored
                }}>
          {SEVERITIES.map(s => <option key={s} value={s}>{s} and above</option>)}
        </select>
        <span className="tiny muted" style={{ flexBasis: '100%', textAlign: 'right' }}>
          Notification channels (Settings → Notifications) only receive alarms when “printer.alarm” is ticked there.
        </span>
        <button className="btn sm" disabled={!alarms.some(a => a.active && !a.acknowledged_at)}
                onClick={() => void run(() => acknowledgeAll(printerId))}>Acknowledge all</button>
      </div>

      {error && <div role="alert" className="small" style={{ color: 'var(--err)', marginBottom: 12 }}>{error}</div>}

      {alarms.length === 0 ? (
        <div className="card muted" style={{ padding: 24, textAlign: 'center' }} data-testid="alarms-empty">
          {status === 'all' ? 'No alarms recorded yet.' : 'No alarms — every printer reports healthy.'}
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table className="tbl" style={{ width: '100%' }}>
            <thead><tr><th>Severity</th><th>Printer</th><th>Problem</th><th>First seen</th><th>State</th><th /></tr></thead>
            <tbody>
              {alarms.map(a => (
                <tr key={a.id} data-testid={`alarm-${a.id}`} style={{ opacity: a.active ? 1 : 0.6 }}>
                  <td><SeverityPill severity={a.severity} /></td>
                  <td>{a.printer_name ?? `#${a.printer_id}`}</td>
                  <td>
                    <div>{a.message}</div>
                    <div className="tiny muted num">
                      {a.code}{a.help_url && <> · <a href={a.help_url} target="_blank" rel="noreferrer">Bambu wiki</a></>}
                    </div>
                  </td>
                  <td className="small num">{a.first_seen.slice(0, 16).replace('T', ' ')}</td>
                  <td className="small">
                    {!a.active ? `resolved ${a.resolved_at?.slice(0, 16).replace('T', ' ')}`
                      : a.acknowledged_at ? 'acknowledged' : 'active'}
                  </td>
                  <td>
                    {a.active && !a.acknowledged_at && (
                      <button className="btn sm" aria-label={`Acknowledge ${a.code}`}
                              onClick={() => void run(() => acknowledgeAlarm(a.id))}>Acknowledge</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
