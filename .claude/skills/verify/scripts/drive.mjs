#!/usr/bin/env node
// Drive the isolated Themis instance with Playwright (from frontend/node_modules) and save evidence.
//   node .claude/skills/verify/scripts/drive.mjs shot <route> <name>   screenshot a route; route WITHOUT leading slash (queue, settings/plugins)
//   node .claude/skills/verify/scripts/drive.mjs plate-clear           journey: needs a printing job (seed --job)
//                                                                      -> Fleet shows "Ready for new work" -> click -> printer eligible again
// Evidence (png + journey log) goes to .claude/verify-run/evidence/ and survives `instance.mjs down`.
import { createRequire } from 'node:module';
import { mkdirSync, appendFileSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
const evidence = join(root, '.claude', 'verify-run', 'evidence');
mkdirSync(evidence, { recursive: true });
const { chromium } = createRequire(join(root, 'frontend', 'package.json'))('playwright-core');
const state = JSON.parse(readFileSync(join(root, '.claude', 'verify-run', 'state.json'), 'utf8'));
const base = `http://127.0.0.1:${state.port}`;
const log = msg => { console.log(msg); appendFileSync(join(evidence, 'journey.log'), `${new Date().toISOString()} ${msg}\n`); };
const api = path => fetch(`${base}/api/v1${path}`).then(r => r.json());

const [cmd, a, b] = process.argv.slice(2);
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', e => consoleErrors.push(String(e)));
try {
  if (cmd === 'shot') {
    // Route is given WITHOUT a leading slash (`queue`, `settings/plugins`): Git Bash rewrites `/queue` to `C:/Program Files/Git/queue`.
    const route = '/' + a.replace(/^\/+/, '');
    await page.goto(base + route);
    await page.waitForLoadState('networkidle');
    await page.screenshot({ path: join(evidence, `${b}.png`), fullPage: true });
    log(`shot ${a} -> ${b}.png`);
  } else if (cmd === 'plate-clear') {
    const before = (await api('/fleet')).find(p => p.name === 'Verify Mock');
    log(`API before: state awaiting_plate_clear=${before?.awaiting_plate_clear}`);
    if (!before?.awaiting_plate_clear) throw new Error('precondition: printer must be awaiting_plate_clear — run `instance.mjs seed --job` and wait ~5s');
    await page.goto(base + '/fleet');
    const btn = page.getByRole('button', { name: /Ready for new work/i });
    await btn.waitFor({ timeout: 15000 });
    await page.screenshot({ path: join(evidence, 'plate-clear-1-before.png') });
    await btn.click();
    await btn.waitFor({ state: 'detached', timeout: 15000 });
    await page.screenshot({ path: join(evidence, 'plate-clear-2-after.png') });
    const after = (await api('/fleet')).find(p => p.name === 'Verify Mock');
    log(`API after: awaiting_plate_clear=${after?.awaiting_plate_clear}`);
    if (after?.awaiting_plate_clear) throw new Error('button disappeared but printer is still awaiting_plate_clear');
    log('PASS plate-clear: UI click -> POST plate-cleared -> printer flag cleared (re-read from API)');
  } else {
    console.error('usage: drive.mjs shot <route> <name> | plate-clear'); process.exitCode = 2;
  }
  if (consoleErrors.length) log(`console errors (${consoleErrors.length}): ${consoleErrors.slice(0, 5).join(' | ')}`);
} catch (e) {
  await page.screenshot({ path: join(evidence, 'FAILURE.png') }).catch(() => {});
  log(`FAIL ${cmd}: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
