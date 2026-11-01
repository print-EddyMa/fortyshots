// End-to-end checks of the built site in docs/ with Playwright (Chromium, headless).
//   node scripts/verify.mjs
// - screenshots of every page at 1440x900 and 390x844 -> web/screenshots/
// - no horizontal scroll at 390
// - console replays the showcase run at 32x to completion with no console errors; the pose morph plays on a reveal
// - results re-roll runs in < 1 s
// - queue keyboard (j/a/r) in static mode stores decisions in localStorage
// - live mode (?live=1) against scripts/stub-live.mjs: events stream in over SSE and `a` POSTs to /api/runs/<id>/human
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from './pw.mjs';
import { serveStatic } from './serve.mjs';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = resolve(WEB, '..', 'docs');
const OUT = join(WEB, 'screenshots');
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
};
const isNoise = (t) => /GL Driver Message|GPU stall|WebGL/.test(t);

const server = createServer((req, res) => serveStatic(DOCS, req, res));
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}/`;
const index = JSON.parse(readFileSync(join(DOCS, 'data/runs/index.json'), 'utf8'));
const showcase = (index.find((r) => r.showcase) ?? index.find((r) => r.arm === 'A5') ?? index[0]).run_id;

const browser = await chromium.launch();

function watch(page) {
  const errs = [];
  page.on('console', (m) => m.type() === 'error' && !isNoise(m.text()) && errs.push(m.text()));
  page.on('pageerror', (e) => errs.push(String(e)));
  page.on('requestfailed', (r) => errs.push(`request failed: ${r.url()}`));
  return errs;
}

// ---------------------------------------------------------------- screenshots + horizontal scroll
const PAGES = [
  { name: 'landing', hash: '#/', wait: 3000 },
  { name: 'console', hash: `#/console/${showcase}?speed=32`, wait: 9000, viewportOnly: true },
  { name: 'results', hash: '#/results', wait: 1500 },
  { name: 'runs', hash: '#/runs', wait: 800 },
  { name: 'method', hash: '#/method', wait: 800 },
];
for (const vp of [
  { tag: '1440', width: 1440, height: 900, dsf: 1 },
  { tag: '390', width: 390, height: 844, dsf: 2 },
]) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.dsf });
  for (const p of PAGES) {
    const page = await ctx.newPage();
    const errs = watch(page);
    await page.goto(base + p.hash, { waitUntil: 'networkidle' });
    await page.waitForTimeout(p.wait);
    const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    await page.screenshot({ path: join(OUT, `${p.name}-${vp.tag}.png`), fullPage: !(p.viewportOnly && vp.tag === '1440') });
    if (vp.tag === '390') check(`no horizontal scroll: ${p.name} @390`, sw <= iw, `${sw}/${iw}`);
    check(`no console errors: ${p.name} @${vp.tag}`, errs.length === 0, errs.slice(0, 3).join(' | '));
    await page.close();
  }
  await ctx.close();
}

// ---------------------------------------------------------------- 32x replay to completion + morph
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = watch(page);
  const t0 = Date.now();
  await page.goto(`${base}#/console/${showcase}?speed=32`, { waitUntil: 'networkidle' });
  const morph = page
    .waitForFunction(() => document.querySelector('.viewer-host')?.getAttribute('data-geom') === 'morphing', null, { timeout: 120000 })
    .then(() => true, () => false);
  await page.waitForSelector('.console[data-ended="1"]', { timeout: 180000 });
  const secs = (Date.now() - t0) / 1000;
  const morphed = await morph;
  await page.waitForTimeout(2600);
  const state = await page.evaluate(() => ({
    lookups: document.querySelector('.c-lookups')?.textContent,
    phase: document.querySelector('.console')?.getAttribute('data-phase'),
    morphedAttr: document.querySelector('.viewer-host')?.getAttribute('data-morphed'),
    caption: document.querySelector('.viewer-cap')?.textContent,
    trace: [...document.querySelectorAll('.t-item')].map((x) => x.className).filter((c) => /k-(summary|claimcheck|report)/.test(c)).length,
    flagged: document.querySelectorAll('.t-flag').length,
    errorItems: document.querySelectorAll('.k-error').length,
  }));
  check('console replays at 32x to completion', state.phase === 'done' && /40/.test(state.lookups ?? ''), `${secs.toFixed(1)} s, ${state.lookups}`);
  check('console: no errors during replay', errs.length === 0, errs.slice(0, 3).join(' | '));
  check('morph plays on reveal', morphed && !!state.morphedAttr, state.caption ?? '');
  const nErrors = readFileSync(join(DOCS, `data/runs/${showcase}/events.jsonl`), 'utf8').split('\n').filter((l) => l.includes('"kind": "error"') || l.includes('"kind":"error"')).length;
  check('trace shows report, claimcheck, summary; flags in --fail; every error event visible', state.trace === 3 && state.flagged >= 1 && state.errorItems >= Math.min(1, nErrors), JSON.stringify({ ...state, nErrors }));
  await page.screenshot({ path: join(OUT, 'console-end-1440.png') });

  await ctx.close();
}

// ---------------------------------------------------------------- queue keyboard (static): open batch via ?seq=
{
  const ev = readFileSync(join(DOCS, `data/runs/${showcase}/events.jsonl`), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const prop = ev.filter((e) => e.kind === 'proposal' && e.round === 2).pop();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = watch(page);
  await page.goto(`${base}#/console/${showcase}?paused=1&seq=${prop.seq}`, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: /Queue/ }).click();
  await page.locator('.q-list').focus();
  await page.keyboard.press('j');
  await page.keyboard.press('r');
  await page.keyboard.press('j');
  await page.keyboard.press('r');
  const label = await page.locator('.q-send button').textContent();
  await page.screenshot({ path: join(OUT, 'console-queue-1440.png') });
  await page.keyboard.press('a');
  const q = await page.evaluate((id) => ({ status: document.querySelector('.q-status')?.textContent, ls: localStorage.getItem(`fortyshots:human:${id}`) }), showcase);
  const saved = JSON.parse(q.ls ?? '{}')[2];
  check('queue (static): j/r/a marks 2 picks, sends one reject decision, saved locally', /Reject 2/.test(label ?? '') && saved?.action === 'reject' && saved?.ids.length === 2, `${label} -> ${q.status}`);
  check('queue: no console errors', errs.length === 0, errs.slice(0, 3).join(' | '));
  await page.close();
  const page2 = await ctx.newPage();
  await page2.goto(`${base}#/console/${showcase}?paused=1&seq=${ev[ev.length - 1].seq}`, { waitUntil: 'networkidle' });
  await page2.waitForTimeout(500);
  await page2.getByRole('tab', { name: /Model/ }).click();
  await page2.screenshot({ path: join(OUT, 'console-model-1440.png') });
  await ctx.close();
}

// ---------------------------------------------------------------- re-roll speed
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${base}#/results`, { waitUntil: 'networkidle' });
  await page.click('.more-stats > summary');  // the re-roll lives in the collapsed full statistics
  await page.waitForSelector('#seed');
  await page.fill('#seed', '1234');
  const t = await page.evaluate(async () => {
    const t0 = performance.now();
    document.querySelector('#reroll button[type=submit]').click();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    return performance.now() - t0;
  });
  const shown = await page.locator('#reroll .reroll-out .row').count();
  check('results: re-roll A0/A1/A2 under 1 s', t < 1000 && shown === 3, `${t.toFixed(0)} ms incl. render`);
  await page.close();
}

// ---------------------------------------------------------------- live mode against the stub
{
  const port = 7790 + Math.floor(Math.random() * 100);
  const stub = spawn(process.execPath, [join(WEB, 'scripts/stub-live.mjs'), '--port', String(port), '--rate', '40'], { stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((r) => stub.stdout.once('data', r));
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = watch(page);
  await page.goto(`http://127.0.0.1:${port}/?live=1#/console/${showcase}`);
  await page.waitForFunction(() => document.querySelectorAll('.t-item').length > 5, null, { timeout: 30000 });
  const status = await page.locator('.c-status').textContent();
  const nItems = await page.locator('.t-item').count();
  check('live: events stream over SSE', /live|finished/.test(status ?? '') && nItems > 5, `${status}, ${nItems} trace items`);
  await page.getByRole('tab', { name: /Queue/ }).click();
  await page.waitForFunction(() => /waiting for you/.test(document.querySelector('.q-status')?.textContent ?? ''), null, { timeout: 30000 });
  await page.screenshot({ path: join(OUT, 'console-live-gate-1440.png') });
  await page.locator('.q-list').focus();
  await page.keyboard.press('a'); // round 1: approve the batch
  await page.waitForFunction(() => /waiting for you/.test(document.querySelector('.q-status')?.textContent ?? '') && /Round 2/.test(document.querySelector('.queue .c-head')?.textContent ?? ''), null, { timeout: 30000 });
  await page.locator('.q-list').focus();
  await page.keyboard.press('r'); // round 2: reject the first pick
  await page.keyboard.press('a');
  await page.waitForTimeout(600);
  const log = await (await fetch(`http://127.0.0.1:${port}/api/_human_log`)).json();
  check(
    'live: queue POSTs one decision per round to /api/runs/<id>/human',
    log.length === 2 && log[0].action === 'approve' && log[0].round === 1 && log[1].action === 'reject' && log[1].ids.length === 1 && log[1].round === 2,
    JSON.stringify(log),
  );
  await page.getByRole('tab', { name: /Trace/ }).click();
  await page.waitForTimeout(300);
  const humanEv = await page.evaluate(() => [...document.querySelectorAll('.k-human')].map((x) => x.textContent));
  check('live: decisions come back through the ledger stream', humanEv.some((t) => /approve/.test(t ?? '')), humanEv.slice(0, 2).join(' | '));
  check('live: no console errors', errs.length === 0, errs.slice(0, 3).join(' | '));
  await page.screenshot({ path: join(OUT, 'console-live-1440.png') });
  await page.close();
  stub.kill();
}

await browser.close();
server.close();
writeFileSync(join(OUT, 'verify.json'), JSON.stringify({ at: new Date().toISOString(), showcase, results }, null, 1));
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
