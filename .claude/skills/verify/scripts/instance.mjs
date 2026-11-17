#!/usr/bin/env node
// Isolated Themis instance for verification: own port, own data dir, built frontend served by FastAPI.
// Usage: node .claude/skills/verify/scripts/instance.mjs <up|doctor|seed|down> [--job]
// Never touches :8001/:5173 or <repo>/data. State lives in .claude/verify-run/ (gitignored).
import { spawn, execFileSync, execSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, openSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
const run = join(root, '.claude', 'verify-run');
const dataDir = join(run, 'data');
const stateFile = join(run, 'state.json');
const port = Number(process.env.VERIFY_PORT || 8011);
const base = `http://127.0.0.1:${port}`;
const win = process.platform === 'win32';
const py = join(root, 'backend', '.venv', win ? 'Scripts/python.exe' : 'bin/python');
const dist = join(root, 'frontend', 'dist');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const readState = () => (existsSync(stateFile) ? JSON.parse(readFileSync(stateFile, 'utf8')) : null);
const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
const api = async (path, init) => {
  const r = await fetch(`${base}/api/v1${path}`, init);
  const text = await r.text();
  if (!r.ok) throw new Error(`${init?.method || 'GET'} ${path} -> ${r.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
};
const healthy = async () => { try { return await api('/health'); } catch { return null; } };

async function up() {
  const st = readState();
  if (st && alive(st.pid) && (await healthy())) { console.log(`already up: pid ${st.pid} ${base}`); return; }
  if (await healthy()) throw new Error(`:${port} is answering but is not our instance (no live pid in state.json). Set VERIFY_PORT to a free port; do not kill it.`);
  if (!existsSync(py)) throw new Error(`no backend venv at ${py} — see CLAUDE.md Commands (python.org Python, pip install -e ".[dev]")`);
  if (!existsSync(join(dist, 'index.html'))) {
    console.log('building frontend (dist missing)...');
    execSync('npm run build', { cwd: join(root, 'frontend'), stdio: 'inherit' });
  }
  rmSync(dataDir, { recursive: true, force: true });
  mkdirSync(dataDir, { recursive: true });
  const log = openSync(join(run, 'backend.log'), 'w');
  const child = spawn(py, ['-m', 'uvicorn', 'app.main:app', '--port', String(port), '--host', '127.0.0.1'], {
    cwd: join(root, 'backend'),
    detached: true,
    stdio: ['ignore', log, log],
    windowsHide: true,
    env: {
      ...process.env,
      THEMIS_MOCK_PRINTERS: '1',                       // enables the mock printer plugin
      THEMIS_DATA_DIR: dataDir,                         // fresh SQLite + library, never <repo>/data
      THEMIS_STATIC_DIR: dist,                          // serve the built SPA; no vite needed
      THEMIS_LOCAL_NETWORKS: '127.0.0.0/8,::1/128',     // loopback is keyless admin (default is 192.168/16 only)
    },
  });
  child.unref();
  writeFileSync(stateFile, JSON.stringify({ pid: child.pid, port, dataDir, startedAt: new Date().toISOString() }));
  for (let i = 0; i < 60; i++) {
    await sleep(1000);
    if (!alive(child.pid)) throw new Error(`backend exited early — see ${join(run, 'backend.log')}`);
    if (await healthy()) { console.log(`up: pid ${child.pid} ${base}`); return; }
  }
  throw new Error(`backend not healthy after 60s — see ${join(run, 'backend.log')}`);
}

async function seed(withJob) {
  let printers = await api('/printers');
  let p = printers.find(x => x.name === 'Verify Mock');
  if (!p) {
    p = await api('/printers', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'Verify Mock', plugin_id: 'mock', manufacturer_id: 'mock', model_id: 'mock', connection_config: {} }) });
  }
  const t = await api(`/printers/${p.id}/test`, { method: 'POST' });   // sets configured=true; a fresh printer is not eligible until then
  if (!t.configured) throw new Error('mock printer did not configure: ' + JSON.stringify(t));
  let files = await api('/files');
  files = Array.isArray(files) ? files : files.files || [];
  let f = files.find(x => x.original_filename === 'verify.gcode');
  if (!f) {
    const fd = new FormData();
    fd.append('file', new Blob(['G28\nG1 X10 Y10\n']), 'verify.gcode');   // pre-sliced gcode: never touches the Laminus slicer
    f = await api('/files/upload', { method: 'POST', body: fd });
  }
  console.log(`seeded: printer #${p.id} (configured), file #${f.id} verify.gcode`);
  if (withJob) {
    const j = await api('/jobs', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ uploaded_file_id: f.id, plate_number: 1, confirm_unknown_eligibility: true, printer_configs: [{ printer_id: p.id, print_profile: '' }] }) });
    console.log(`job #${j.id} created (status ${j.status}); the queue claims it within seconds -> printing, printer awaiting_plate_clear`);
  }
}

async function doctor() {
  const st = readState();
  const out = [];
  let bad = false;
  const check = (ok, msg) => { out.push(`${ok ? 'OK  ' : 'FAIL'} ${msg}`); if (!ok) bad = true; };
  check(!!st, 'state.json present (instance.mjs up was run)');
  if (st) check(alive(st.pid), `backend pid ${st.pid} alive`);
  const h = await healthy();
  check(!!h, `GET ${base}/api/v1/health answers`);
  if (h) {
    let head = '';
    try { head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root }).toString().trim(); } catch {}
    check(!head || h.git_sha === head || h.git_sha === 'unknown', `backend git_sha ${String(h.git_sha).slice(0, 8)} matches HEAD ${head.slice(0, 8)} (stale instance -> down, up)`);
    const html = await (await fetch(`${base}/`)).text();
    check(html.includes('<div id="root"'), 'SPA served at / (THEMIS_STATIC_DIR)');
    const me = await fetch(`${base}/api/v1/auth/me`).then(r => r.json()).catch(() => null);
    check(!!me?.role, `loopback is keyless ${me?.role}`);
    try { const ps = await api('/printers'); check(ps.some(p => p.configured), `a configured printer exists (${ps.length} printers) — run seed if not`); } catch (e) { check(false, String(e)); }
  }
  if (existsSync(join(dist, 'index.html'))) {
    const newest = execFileSync('git', ['log', '-1', '--format=%ct', '--', 'frontend/src'], { cwd: root }).toString().trim();
    const built = Math.floor(statSync(join(dist, 'index.html')).mtimeMs / 1000);
    check(!newest || built >= Number(newest), 'frontend/dist is at least as new as the last commit touching frontend/src (else: npm run build in frontend/)');
  }
  console.log(out.join('\n'));
  if (bad) process.exitCode = 1;
}

function down() {
  const st = readState();
  if (st && alive(st.pid)) {
    try {
      if (win) execFileSync('taskkill', ['/PID', String(st.pid), '/T', '/F'], { stdio: 'ignore' });
      else process.kill(-st.pid, 'SIGTERM');
    } catch (e) { console.log('kill failed:', e.message); }
    console.log(`stopped pid ${st.pid}`);
  } else console.log('no live instance of ours');
  rmSync(dataDir, { recursive: true, force: true });      // scratch data only; evidence/ and backend.log are kept
  rmSync(stateFile, { force: true });
}

mkdirSync(run, { recursive: true });
const [cmd, ...rest] = process.argv.slice(2);
try {
  if (cmd === 'up') { await up(); await seed(false); }
  else if (cmd === 'seed') await seed(rest.includes('--job'));
  else if (cmd === 'doctor') await doctor();
  else if (cmd === 'down') down();
  else { console.error('usage: instance.mjs <up|doctor|seed [--job]|down>'); process.exitCode = 2; }
} catch (e) { console.error('ERROR:', e.message); process.exitCode = 1; }
