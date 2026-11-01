// Records the browser B-roll for the demo video from the built static site (docs/), per VIDEO.md.
//
//   node web/scripts/video/record-broll.mjs                 all shots, 1920x1080 and 1080x1920
//   node web/scripts/video/record-broll.mjs --only console-8x,dft-reveal --format desktop
//   node web/scripts/video/record-broll.mjs --run <run_id>  record a run other than the showcase
//
// Output: media/broll/<shot>-1920x1080.webm, media/broll/<shot>-1080x1920.webm and media/broll/shots.json.
//
// How the frames stay sharp: Playwright's recorder captures CSS pixels, not device pixels, so a 1440 px wide
// page would come out 1440 px wide. Each shot therefore loads the site in an iframe inside a 1920x1080 (or
// 1080x1920) wrapper page and scales the iframe with a CSS transform. Chrome re-rasterises text and SVG at
// the final scale, the iframe still sees its own viewport (1440x810 desktop, 396x704 mobile, so the site's own
// media queries pick the desktop or the mobile layout), and window.devicePixelRatio is raised inside the
// iframe so the 3Dmol canvas renders at the output resolution. The same transform doubles as a camera:
// `cam.to(selector)` eases in on part of the page (a close-up), `cam.reset()` eases back out.
//
// Everything is driven by the run's ledger (events.jsonl), never by fixed sequence numbers or run ids, so the
// same script works when real runs replace the fixtures.
//
// Explainer scenes (scenes/*.html: humidity, bottleneck, loop, split, dflash, result) are shots too. They are
// pure functions of time (scenes/scene.js), so instead of a screen recording each frame is rendered at an exact
// t and piped to ffmpeg at 30 fps: the same clip every time, with no dropped frames. Their numbers come from
// scenes/data.py, which runs first. Stills for checking a scene without recording it:
//   node web/scripts/video/record-broll.mjs --only humidity,result --stills 1,4.5,9   -> media/scene-stills/*.png
import { createServer } from 'node:http';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../pw.mjs';
import { serveStatic } from '../serve.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..', '..');
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i >= 0 ? process.argv[i + 1] : d;
};
const DOCS = resolve(ROOT, arg('--docs', 'docs'));
const OUT = resolve(ROOT, arg('--out', 'media/broll'));
const ONLY = (arg('--only', '') || '').split(',').filter(Boolean);
const FORMAT = arg('--format', 'both'); // desktop | mobile | both
const STILLS = (arg('--stills', '') || '').split(',').filter(Boolean).map(Number);
const SCENES = join(HERE, 'scenes');
const VERBOSE = process.argv.includes('--verbose');

// ------------------------------------------------------------------------------------------- formats
// out: the video frame. vw/vh: the viewport the site sees. scale = out / viewport.
const FORMATS = {
  desktop: { tag: '1920x1080', out: [1920, 1080], vw: 1440, vh: 810 },
  mobile: { tag: '1080x1920', out: [1080, 1920], vw: 396, vh: 704 },
};
for (const f of Object.values(FORMATS)) f.scale = f.out[0] / f.vw;

// ------------------------------------------------------------------------------------------- run choice
const index = JSON.parse(readFileSync(join(DOCS, 'data/runs/index.json'), 'utf8'));
function chooseRun() {
  const forced = arg('--run', null);
  if (forced) {
    const r = index.find((x) => x.run_id === forced);
    if (!r) throw new Error(`--run ${forced} is not in docs/data/runs/index.json`);
    return { run: r, why: '--run' };
  }
  const flagged = index.find((r) => r.showcase);
  if (flagged) return { run: flagged, why: 'showcase flag in runs/index.json' };
  const a5p1 = index.find((r) => r.arm === 'A5' && r.pool === 'P1');
  if (a5p1) return { run: a5p1, why: 'first A5 run on P1' };
  if (index[0]) return { run: index[0], why: 'first run in the index (fixture)' };
  throw new Error('docs/data/runs/index.json lists no runs; build the site first (cd web && npm run build)');
}
const { run, why } = chooseRun();
const RUN = run.run_id;
const EVENTS = readFileSync(join(DOCS, 'data/runs', RUN, 'events.jsonl'), 'utf8')
  .split('\n')
  .filter(Boolean)
  .map((l) => JSON.parse(l));

// Same compression as web/src/lib/replay.ts timeline(): gaps capped at 20 s, floored at 220 ms.
function timeline(events, capMs = 20000, minMs = 220) {
  const out = [];
  let acc = 0;
  let prev = null;
  for (const e of events) {
    const t = Date.parse(e.t);
    if (prev != null && Number.isFinite(t)) acc += Math.min(Math.max(minMs, t - prev), capMs);
    if (Number.isFinite(t)) prev = t;
    out.push(acc);
  }
  return out;
}
const TL = timeline(EVENTS);
const TOTAL = TL[TL.length - 1] ?? 0;
const idx = (pred, from = 0) => {
  for (let i = from; i < EVENTS.length; i++) if (pred(EVENTS[i], i)) return i;
  return -1;
};
const lastIdx = (pred) => {
  for (let i = EVENTS.length - 1; i >= 0; i--) if (pred(EVENTS[i], i)) return i;
  return -1;
};
const vt = (i) => (i >= 0 ? TL[i] : 0);
const clampVt = (ms) => Math.max(0, Math.min(TOTAL, ms));

// Key moments in the ledger. Each falls back to something sensible when a run lacks it.
function moments() {
  const m = {};
  const firstTool = idx((e) => e.kind === 'tool_call' && e.round >= 1 && e.role === 'director');
  m.start1x = clampVt(firstTool >= 0 ? vt(firstTool) - 6000 : 0);
  let prevHits = 0;
  const firstHit = idx((e) => {
    if (e.kind !== 'dft_result') return false;
    const h = e.payload?.hits_total ?? 0;
    const isNew = h > prevHits;
    prevHits = h;
    return isNew;
  });
  const firstResult = idx((e) => e.kind === 'dft_result');
  m.firstHit = firstHit;
  m.reveal = firstHit >= 0 ? firstHit : firstResult;
  // The richest reveal for a close-up: the dft_result with the most new hits.
  let best = -1;
  let bestNew = -1;
  prevHits = 0;
  EVENTS.forEach((e, i) => {
    if (e.kind !== 'dft_result') return;
    const h = e.payload?.hits_total ?? 0;
    if (h - prevHits > bestNew) {
      bestNew = h - prevHits;
      best = i;
    }
    prevHits = h;
  });
  m.bestReveal = best >= 0 ? best : m.reveal;
  m.veto = idx((e) => e.kind === 'critique' && (e.payload?.vetoes ?? []).length > 0);
  if (m.veto < 0) m.veto = idx((e) => e.kind === 'critique');
  // calibrate_oracle: prefer a round whose critique reports a visual rho (the Critic read the plot).
  const cals = EVENTS.map((e, i) => (e.kind === 'calibration' ? i : -1)).filter((i) => i >= 0);
  const critAfter = (i) => idx((e) => e.kind === 'critique' && e.round === EVENTS[i].round, i);
  m.cal = cals.find((i) => {
    const c = critAfter(i);
    return c >= 0 && EVENTS[c].payload?.visual_rho != null;
  }) ?? cals[0] ?? -1;
  if (m.cal >= 0) {
    const c = EVENTS[m.cal];
    m.calCall = lastIdx((e, i) => i <= m.cal && e.kind === 'tool_call' && e.payload?.name === 'calibrate_oracle');
    if (m.calCall < 0) m.calCall = m.cal;
    // The trace line that follows: the next reasoning/message from the Director after the calibration.
    m.calNext = idx((e) => (e.kind === 'reasoning' || e.kind === 'message') && e.round === c.round, m.cal);
    m.calCrit = critAfter(m.cal);
  }
  m.report = idx((e) => e.kind === 'report');
  m.claim = idx((e) => e.kind === 'claimcheck');
  // Approval: the last proposal of a round before its human/dft_order event (an open batch).
  const humans = EVENTS.map((e, i) => (e.kind === 'human' || e.kind === 'dft_order' ? i : -1)).filter((i) => i >= 0);
  m.openBatch = -1;
  for (const r of [2, 1, 3, 4]) {
    const gate = humans.find((i) => EVENTS[i].round === r);
    if (gate == null) continue;
    const p = lastIdx((e, i) => i < gate && e.kind === 'proposal' && e.round === r);
    if (p >= 0) {
      m.openBatch = p;
      break;
    }
  }
  m.mutations = EVENTS.some((e) => e.kind === 'mutation');
  m.last = EVENTS.length - 1;
  return m;
}
const M = moments();

// ------------------------------------------------------------------------------------------- server
const wrapperHtml = (f) => `<!doctype html><html><head><meta charset="utf-8"><title>broll</title><style>
html,body{margin:0;padding:0;width:${f.out[0]}px;height:${f.out[1]}px;overflow:hidden;background:#0d0d0d}
#cam{position:absolute;left:0;top:0;width:${f.vw}px;height:${f.vh}px;transform-origin:0 0;transform:scale(${f.scale})}
iframe{display:block;border:0;width:${f.vw}px;height:${f.vh}px;background:#0d0d0d}
</style></head><body><div id="cam"><iframe id="site" title="site"></iframe></div></body></html>`;

// ------------------------------------------------------------------------------------------- cards
// Two HTML cards recorded like any other shot, styled like the site (Geist from the built bundle, hairlines).
// "built": what was made during the event and what was used, with the commit count from git.
// "close": the clone command typed into a terminal line, then the repo and site URLs.
const siteCfg = JSON.parse(readFileSync(join(ROOT, 'web', 'site.config.json'), 'utf8'));
const short = (u) => String(u ?? '').replace(/^https?:\/\//, '').replace(/\/$/, '');
function gitCount() {
  try {
    return execFileSync('git', ['-C', ROOT, 'rev-list', '--count', 'hackathon-start..HEAD']).toString().trim();
  } catch {
    return null;
  }
}
const COMMITS = gitCount();
function fontFaces() {
  const dir = join(DOCS, 'assets');
  let files = [];
  try {
    files = readdirSync(dir);
  } catch {
    /* no build */
  }
  const sans = files.find((f) => /^geist-latin-wght-normal-.*\.woff2$/.test(f));
  const mono = files.find((f) => /^geist-mono-latin-wght-normal-.*\.woff2$/.test(f));
  return [
    sans && `@font-face{font-family:Geist;src:url(/assets/${sans}) format('woff2');font-weight:100 900}`,
    mono && `@font-face{font-family:'Geist Mono';src:url(/assets/${mono}) format('woff2');font-weight:100 900}`,
  ]
    .filter(Boolean)
    .join('');
}
const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
function cardHtml(kind) {
  const base = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${fontFaces()}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{font-family:Geist,system-ui,sans-serif;-webkit-font-smoothing:antialiased;font-size:15px;line-height:24px}
.mono{font-family:'Geist Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums slashed-zero;font-feature-settings:'zero' 1}
@keyframes up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.in{opacity:0;animation:up .42s cubic-bezier(.16,1,.3,1) forwards}
</style>`;
  if (kind === 'built') {
    const built = [
      ['The four-role agent', 'Director, Proposer, Critic, Reporter · 7 tools'],
      ['The lab runtime', 'one command on a 24 GB MacBook · live approval'],
      ['The benchmark', 'pre-registered · blind DFT scoring · verifier'],
      ['This site and console', 'replay of every ledger · chemistry views'],
      ['The materials proposer', 'mutant frameworks · MACE relaxation'],
    ];
    const used = [
      ['Muse Glimmer 30B + DFlash drafter', 'Meta · open weights'],
      ['ODAC23 DFT data + EquiformerV2', 'Meta FAIR'],
      ['MACE-MP-0', 'cheap physics'],
      ['llama.cpp', 'local inference'],
    ];
    const rows = (xs, d0) =>
      xs.map(([a, b], i) => `<div class="row in" style="animation-delay:${d0 + i * 0.16}s"><span>${esc(a)}</span><span class="d">${esc(b)}</span></div>`).join('');
    return `${base}<style>
body{background:#fafafa;color:#0a0a0a;display:flex;align-items:center;justify-content:center}
.wrap{width:min(1080px,100% - 48px)}
h1{font-size:clamp(34px,4.4vw,60px);line-height:1;letter-spacing:-.045em;font-weight:500;margin:0 0 36px}
h1 span{color:#8c8c8c}
.cols{display:grid;grid-template-columns:1.15fr 1fr;gap:48px}
.lab{font-size:13px;color:#666;padding-bottom:10px;border-bottom:1px solid #d4d4d4}
.row{display:flex;flex-direction:column;padding:11px 0;border-bottom:1px solid #e5e5e5}
.row .d{color:#737373;font-size:13px;line-height:20px}
.foot{margin-top:32px;font-size:13px;color:#666;display:flex;gap:10px;align-items:baseline}
.foot b{font-weight:400;color:#0a0a0a;font-size:22px}
@media (max-width:640px){.cols{grid-template-columns:1fr;gap:28px}h1{margin-bottom:24px}.row{padding:8px 0}}
</style></head><body><div class="wrap">
<h1 class="in">Built this week. <span>Oct 27 – Nov 4, 2026.</span></h1>
<div class="cols"><div><div class="lab in" style="animation-delay:.2s">Built during the hackathon</div>${rows(built, 0.35)}</div>
<div><div class="lab in" style="animation-delay:.25s">Used</div>${rows(used, 1.2)}</div></div>
<div class="foot mono in" style="animation-delay:2s"><b>${esc(COMMITS ?? '—')}</b> commits since hackathon-start <span style="color:#737373">· git rev-list --count hackathon-start..HEAD</span></div>
</div></body></html>`;
  }
  const cmd = `git clone ${short(siteCfg.REPO_URL)} && ./lab verify`;
  return `${base}<style>
body{background:#0d0d0d;color:#ededed;display:flex;align-items:center;justify-content:center}
.wrap{width:min(1080px,100% - 48px)}
.term{font-size:clamp(15px,1.9vw,26px);line-height:1.5;letter-spacing:-.02em;padding:22px 26px;border:1px solid #333;border-radius:12px;background:#141414;overflow-wrap:anywhere}
.term .p{color:#858585}
.cur{display:inline-block;width:.6em;height:1.1em;vertical-align:-.15em;background:#f59e4b;animation:blink 1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.urls{margin-top:28px;display:grid;gap:6px;font-size:clamp(14px,1.5vw,20px);color:#8b8b8b}
.urls b{color:#ededed;font-weight:400}
h2{font-size:clamp(28px,3.4vw,46px);line-height:1.05;letter-spacing:-.04em;font-weight:500;margin:0 0 28px}
h2 span{color:#858585}
</style></head><body><div class="wrap">
<h2 class="in">Open. <span>Clone it and check every number.</span></h2>
<div class="term mono in" style="animation-delay:.3s"><span class="p">$ </span><span id="t"></span><span class="cur"></span></div>
<div class="urls mono"><div class="in" id="u1" style="animation-play-state:paused">code <b>${esc(short(siteCfg.REPO_URL))}</b></div><div class="in" id="u2" style="animation-play-state:paused">site <b>${esc(short(siteCfg.SITE_URL))}</b></div></div>
</div><script>
const cmd=${JSON.stringify(cmd)};let i=0;const t=document.getElementById('t');
setTimeout(function k(){t.textContent=cmd.slice(0,++i);if(i<cmd.length)setTimeout(k,38+Math.random()*30);else{['u1','u2'].forEach((id,j)=>{const e=document.getElementById(id);e.style.animationDelay=(0.3+j*0.25)+'s';e.style.animationPlayState='running';});}},900);
</script></body></html>`;
}

const server = createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/__card.html') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' });
    return res.end(cardHtml(u.searchParams.get('k')));
  }
  if (u.pathname === '/__scenes/fonts.css') {
    res.writeHead(200, { 'content-type': 'text/css', 'cache-control': 'no-cache' });
    return res.end(fontFaces());
  }
  if (u.pathname.startsWith('/__scenes/')) return serveStatic(SCENES, { url: req.url.replace('/__scenes', '') }, res);
  if (u.pathname === '/__broll.html') {
    const base = FORMATS[u.searchParams.get('f')] ?? FORMATS.desktop;
    const vw = Number(u.searchParams.get('vw')) || base.vw;
    const f = { ...base, vw, vh: Math.round(base.out[1] / (base.out[0] / vw)), scale: base.out[0] / vw };
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' });
    return res.end(wrapperHtml(f));
  }
  serveStatic(DOCS, req, res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// ------------------------------------------------------------------------------------------- shot context
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Close-up framing for the console on desktop: a wider right column (trace, queue, model tab) so a 16:9
// close-up of it is not half candidate table. Same components, only the column widths change.
const FOCUS_CSS = `
@media (min-width: 1280px) {
  .console-page .c-grid { grid-template-columns: 220px minmax(0, 1fr) 640px !important; }
}
.c-right .tool-img { max-width: 300px !important; }
.c-right .viewer-frame { aspect-ratio: auto !important; height: 230px !important; }
`;

async function makeShot(browser, fmt, opts = {}) {
  const base = FORMATS[fmt];
  const vw = (fmt === 'desktop' && opts.vw) || base.vw;
  const f = { ...base, vw, vh: Math.round(base.out[1] / (base.out[0] / vw)), scale: base.out[0] / vw };
  const tmpDir = join(OUT, '.tmp');
  mkdirSync(tmpDir, { recursive: true });
  const zoomMax = opts.zoom ?? 1;
  const dpr = Math.min(4, f.scale * zoomMax);
  const ctx = await browser.newContext({
    viewport: { width: f.out[0], height: f.out[1] },
    deviceScaleFactor: 1,
    recordVideo: { dir: tmpDir, size: { width: f.out[0], height: f.out[1] } },
    reducedMotion: 'no-preference',
    colorScheme: 'dark',
  });
  // Inside the site iframe: report the output resolution as the device pixel ratio (crisp WebGL canvas).
  await ctx.addInitScript((d) => {
    if (window.top !== window) Object.defineProperty(window, 'devicePixelRatio', { get: () => d, configurable: true });
  }, dpr);
  const page = await ctx.newPage();
  const t0 = Date.now();
  await page.goto(`${BASE}__broll.html?f=${fmt}&vw=${vw}`);
  const frame = await (await page.$('#site')).contentFrame();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => m.type() === 'error' && !/GL Driver|GPU stall|WebGL|Automatic fallback to software/.test(m.text()) && errors.push(m.text()));
  let inAt = null;
  const marks = {};
  let camZoom = 1;

  const s = {
    fmt,
    f,
    page,
    frame,
    errors,
    mobile: fmt === 'mobile',
    now: () => (Date.now() - t0) / 1000,
    async go(hash) {
      await frame.goto(`${BASE}${hash}`, { waitUntil: 'networkidle' });
      if (opts.focus && fmt === 'desktop') await frame.addStyleTag({ content: FOCUS_CSS });
      await frame.evaluate(() => document.fonts.ready);
    },
    /** Put data-broll=<name> on the element around heading text (for scrolling and the camera). */
    async tag(text, name, levels = 1) {
      await frame.evaluate(
        ([text, name, levels]) => {
          let el = [...document.querySelectorAll('h1,h2,h3')].find((h) => (h.textContent ?? '').includes(text));
          for (let i = 0; el && i < levels; i++) el = el.parentElement;
          if (el) el.setAttribute('data-broll', name);
        },
        [text, name, levels],
      );
      return `[data-broll="${name}"]`;
    },
    async hold(ms) {
      await sleep(ms);
    },
    /** Start of the usable part of the clip. */
    start() {
      inAt = s.now();
      marks.in = 0;
    },
    mark(name) {
      if (inAt == null) s.start();
      marks[name] = +(s.now() - inAt).toFixed(2);
    },
    /** Eased page scroll inside the site so `target` (selector, {text}, or y) sits `offset` px below the top. */
    async scroll(target, ms = 1400, offset = 72) {
      await frame.evaluate(
        async ([t, ms, off]) => {
          const find = (t) => {
            if (typeof t === 'number') return null;
            if (typeof t === 'string') return document.querySelector(t);
            const els = [...document.querySelectorAll(t.sel ?? 'h1,h2,h3')];
            return els.find((e) => (e.textContent ?? '').includes(t.text)) ?? null;
          };
          const el = find(t);
          const y0 = window.scrollY;
          let y1 = el ? el.getBoundingClientRect().top + window.scrollY - off : typeof t === 'number' ? t : y0;
          y1 = Math.max(0, Math.min(y1, document.documentElement.scrollHeight - window.innerHeight));
          const start = performance.now();
          await new Promise((res) => {
            const step = (now) => {
              const k = Math.min(1, (now - start) / ms);
              const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
              window.scrollTo({ top: y0 + (y1 - y0) * e, behavior: 'instant' });
              if (k < 1) requestAnimationFrame(step);
              else res();
            };
            requestAnimationFrame(step);
          });
        },
        [target, ms, offset],
      );
    },
    /** Camera: ease the wrapper transform so the union of `sels` fills the frame (with padding). */
    async cam(sels, ms = 1200, pad = 16, maxZoom = zoomMax, anchor = 'center') {
      const rect = sels
        ? await frame.evaluate(
            ([sels, pad]) => {
              const rs = sels.map((q) => document.querySelector(q)?.getBoundingClientRect()).filter(Boolean);
              if (!rs.length) return null;
              const l = Math.min(...rs.map((r) => r.left)) - pad;
              const t = Math.min(...rs.map((r) => r.top)) - pad;
              const r = Math.max(...rs.map((r) => r.right)) + pad;
              const b = Math.max(...rs.map((r) => r.bottom)) + pad;
              return { l, t, w: r - l, h: b - t };
            },
            [sels, pad],
          )
        : null;
      let z = 1;
      let cx = f.vw / 2;
      let cy = f.vh / 2;
      if (rect) {
        z = Math.max(1, Math.min(maxZoom, f.vw / rect.w, f.vh / rect.h));
        cx = rect.l + rect.w / 2;
        cy = rect.t + rect.h / 2;
        // a panel taller than the window: show its end (where new trace lines appear)
        if (anchor === 'bottom' && rect.h > f.vh / z) cy = rect.t + rect.h - f.vh / z / 2;
      }
      // keep the visible window inside the iframe
      const hw = f.vw / z / 2;
      const hh = f.vh / z / 2;
      cx = Math.max(hw, Math.min(f.vw - hw, cx));
      cy = Math.max(hh, Math.min(f.vh - hh, cy));
      const target = { z, x: -(cx - hw) * z * f.scale, y: -(cy - hh) * z * f.scale };
      camZoom = z;
      await page.evaluate(
        async ([tg, ms, base]) => {
          const el = document.getElementById('cam');
          const cur = el._cam ?? { z: 1, x: 0, y: 0 };
          const start = performance.now();
          await new Promise((res) => {
            const step = (now) => {
              const k = ms <= 0 ? 1 : Math.min(1, (now - start) / ms);
              const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
              const z = cur.z + (tg.z - cur.z) * e;
              const x = cur.x + (tg.x - cur.x) * e;
              const y = cur.y + (tg.y - cur.y) * e;
              el.style.transform = `translate(${x}px, ${y}px) scale(${z * base})`;
              if (k < 1) requestAnimationFrame(step);
              else {
                el._cam = tg;
                res();
              }
            };
            requestAnimationFrame(step);
          });
        },
        [target, ms, f.scale],
      );
      return z;
    },
    camZoom: () => camZoom,
    // ---------------------------------------------------------------- console helpers
    async consoleReady() {
      await frame.waitForSelector('.console .c-lookups', { timeout: 30000 });
      await frame.waitForFunction(() => Number(document.querySelector('.console')?.getAttribute('data-cursor') ?? 0) >= 0);
      await frame.waitForSelector('.c-scrub input', { timeout: 30000 }).catch(() => undefined);
      await sleep(400);
    },
    /** Set the replay position exactly (no visible drag). */
    async seekExact(ms) {
      await frame.evaluate((v) => {
        const el = document.querySelector('.c-scrub input');
        if (!el) return;
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        set.call(el, String(v));
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }, Math.round(clampVt(ms) / 100) * 100);
      await sleep(250);
    },
    /** Drag the scrubber thumb to `ms` with the mouse, then land exactly on it. */
    async seekDrag(ms, dur = 700) {
      const box = await frame.evaluate(() => {
        const el = document.querySelector('.c-scrub input');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { l: r.left, t: r.top, w: r.width, h: r.height, v: Number(el.value), max: Number(el.max) };
      });
      if (!box) return s.seekExact(ms);
      const k = f.scale * camZoom; // only used without camera; seeks happen at zoom 1
      const thumb = 8;
      const xAt = (v) => (box.l + thumb + (box.w - 2 * thumb) * (v / Math.max(1, box.max))) * k;
      const y = (box.t + box.h / 2) * k;
      await page.mouse.move(xAt(box.v), y);
      await page.mouse.down();
      const steps = Math.max(4, Math.round(dur / 150));
      await page.mouse.move(xAt(clampVt(ms)), y, { steps });
      await page.mouse.up();
      await s.seekExact(ms);
    },
    /** Click like a person when the control is on screen; otherwise fire the click without scrolling the shot. */
    async tap(loc) {
      const onScreen =
        camZoom <= 1.001 &&
        (await loc.evaluate((el) => {
          const r = el.getBoundingClientRect();
          return r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth;
        }));
      if (onScreen) await loc.click();
      else await loc.dispatchEvent('click');
    },
    async speed(x) {
      await s.tap(frame.getByRole('group', { name: 'Replay speed' }).getByRole('button', { name: `${x}×` }));
    },
    async play() {
      const label = await frame.locator('.c-play').textContent();
      if (/Play|Replay/.test(label ?? '')) await s.tap(frame.locator('.c-play'));
    },
    async pause() {
      const label = await frame.locator('.c-play').textContent();
      if (/Pause/.test(label ?? '')) await s.tap(frame.locator('.c-play'));
    },
    async tab(name) {
      await s.tap(frame.getByRole('tab', { name: new RegExp(name) }));
    },
    async finish() {
      const usable = inAt == null ? s.now() : s.now() - inAt;
      const trimIn = inAt ?? 0;
      await ctx.close();
      return { video: await page.video().path(), trimIn, usable, marks };
    },
  };
  return s;
}

// ------------------------------------------------------------------------------------------- scenes
// Frame-exact capture of a scene page: seek(t) for every frame, screenshot, pipe PNGs to ffmpeg (H.264, CRF 12).
async function openScene(browser, fmt, id) {
  const f = FORMATS[fmt];
  const ctx = await browser.newContext({ viewport: { width: f.out[0], height: f.out[1] }, deviceScaleFactor: 1, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await page.goto(`${BASE}__scenes/${id}.html?capture=1`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__scene && window.__scene.ready, null, { timeout: 30000 });
  return { ctx, page, errors, f };
}
async function sceneStills(browser, fmt, shot) {
  const dir = join(ROOT, 'media', 'scene-stills');
  mkdirSync(dir, { recursive: true });
  const { ctx, page, errors } = await openScene(browser, fmt, shot.id);
  const files = [];
  for (const t of STILLS) {
    await page.evaluate((t) => window.__scene.seek(t), t);
    const file = join(dir, `${shot.id}-${FORMATS[fmt].tag}-t${t.toFixed(2)}.png`);
    await page.screenshot({ path: file });
    files.push(relative(ROOT, file));
  }
  await ctx.close();
  console.log(`[broll] stills ${shot.id} ${fmt}: ${files.join(' ')}${errors.length ? `  page errors: ${errors.join(' | ')}` : ''}`);
}
async function recordScene(browser, fmt, shot, file) {
  const FPS = 30;
  const { ctx, page, errors } = await openScene(browser, fmt, shot.id);
  const duration = await page.evaluate(() => window.__scene.duration);
  const n = Math.round(duration * FPS);
  const ff = spawn('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'image2pipe', '-c:v', 'png', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', '-r', String(FPS), '-movflags', '+faststart', file], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', (c) => (c === 0 ? res() : rej(new Error(`ffmpeg exited ${c}`)))));
  for (let i = 0; i < n; i++) {
    await page.evaluate((t) => window.__scene.seek(t), i / FPS);
    const png = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await done;
  await ctx.close();
  return { usable: n / FPS, errors, marks: { in: 0, ...(shot.marks ?? {}) } };
}

// ------------------------------------------------------------------------------------------- shots
const consoleHash = (q = '') => `#/console/${encodeURIComponent(RUN)}${q ? `?${q}` : ''}`;
const hasEvent = (i) => i >= 0;
const SEC = 1000;

const SHOTS = [
  {
    id: 'hero-orbit',
    storyboard: '0:00–0:10 (Hook)',
    xclip: '0–4 s',
    speed: 1,
    zoom: 1.6,
    desc: 'Landing hero: a real DFT hit as a 2x2x2 supercell inside its pore mesh, orbiting; CO2 moves from the ODAC initial placement to the DFT-relaxed pose.',
    async record(s) {
      await s.go('#/');
      // the 3D view loads when it nears the viewport, so scroll first
      await s.scroll('.specimen', 0, s.mobile ? 120 : 40);
      await s.frame.waitForSelector('.specimen-host[data-ready="1"]', { timeout: 60000 });
      // the burned-in subtitles sit at the bottom of the frame: move the specimen caption to the top for this shot
      await s.frame.addStyleTag({ content: '.specimen-cap{bottom:auto;top:44px}.specimen-legend{flex-basis:auto}' });
      if (!s.mobile) await s.cam(['.specimen-frame'], 0, -4);
      await s.hold(1500);
      s.start();
      await s.tap(s.frame.locator('.specimen-ctl button', { hasText: 'replay' }));
      s.mark('relax');
      await s.hold(15000);
    },
  },
  {
    id: 'why-hard',
    storyboard: '0:10–0:24 (The problem)',
    xclip: '4–10 s',
    speed: 1,
    vw: 1120,
    zoom: 1.5,
    desc: 'Results page: DFT CO2 vs water binding for the 3,000 MOFs of the six pools, hits filled, with the share where water wins.',
    async record(s) {
      await s.go('#/results');
      await s.frame.waitForSelector('.stats');
      const sec = await s.tag('Why this is hard', 'whyhard', 1);
      await s.scroll(sec, 0, s.mobile ? 80 : 100);
      if (!s.mobile) await s.cam([sec], 0, 20);
      await s.hold(1200);
      s.start();
      await s.hold(14000);
    },
  },
  {
    id: 'atlas-path',
    storyboard: '0:10–0:24 (map) and 0:52–1:40 (path)',
    xclip: '22–30 s',
    speed: 1,
    zoom: 1.5,
    desc: "Atlas of 3,000 MOFs (t-SNE of label-free descriptors + MACE); the showcase run's 40 purchases grow round by round, hits flash.",
    async record(s) {
      await s.go('#/?atlas=paused');
      await s.frame.waitForSelector('#atlas canvas', { timeout: 30000 });
      await s.scroll('#atlas', 0, s.mobile ? 60 : 70);
      if (!s.mobile) await s.cam(['#atlas .atlas'], 0, 18);
      await s.hold(1200);
      s.start();
      s.mark('overview');
      await s.hold(5000);
      await s.tap(s.frame.locator('.atlas-replay'));
      s.mark('play');
      await s.hold(12000);
      s.mark('done');
      await s.hold(2500);
    },
  },
  {
    id: 'periodic-table',
    storyboard: 'spare (Results: which metals)',
    xclip: null,
    speed: 1,
    vw: 1120,
    zoom: 1.4,
    desc: 'Results page: the periodic table of DFT hit rates by metal; focus moves across copper, zinc, lithium and uranium.',
    async record(s) {
      await s.go('#/results');
      await s.frame.waitForSelector('.ptable');
      await s.scroll('#metals', 0, s.mobile ? 60 : 80);
      if (!s.mobile) await s.cam(['#metals'], 0, 16);
      await s.hold(1200);
      s.start();
      await s.hold(1800);
      for (const name of ['copper', 'zinc', 'lithium', 'uranium']) {
        await s.frame.locator(`.pt-cell[aria-label^="${name}"]`).focus().catch(() => undefined);
        s.mark(name);
        await s.hold(2300);
      }
    },
  },
  {
    id: 'binding-site',
    storyboard: '0:52–1:40 (the reveal close-up)',
    xclip: '10–22 s',
    speed: 1,
    zoom: 2.4,
    skipIf: () => !hasEvent(M.bestReveal) && 'run has no dft_result',
    desc: 'Console viewer after a reveal: binding-site close-up with dashed contacts and distances from the DFT-relaxed geometry, then DDEC6 charges.',
    async record(s) {
      await s.go(consoleHash(`paused=1&speed=1&seq=${EVENTS[M.bestReveal].seq}`));
      await s.consoleReady();
      await s.frame.waitForSelector('.viewer-host[data-status="ready"]', { timeout: 30000 });
      if (s.mobile) await s.scroll('.viewer', 0, 60);
      else await s.cam(['.viewer'], 0, 8);
      await s.hold(1500);
      s.start();
      await s.hold(800);
      const site = s.frame.getByRole('button', { name: 'site', exact: true });
      if (await site.count()) {
        await s.tap(site);
        await s.hold(300);
        await s.tap(site);
      }
      s.mark('site');
      await s.hold(6000);
      await s.tap(s.frame.getByRole('button', { name: 'charge', exact: true })).catch(() => undefined);
      s.mark('charge');
      await s.hold(5500);
    },
  },
  {
    id: 'built-card',
    storyboard: '0:42–0:52 (Built this week)',
    xclip: null,
    speed: 1,
    desc: 'Card: what was built during the hackathon and what was used, with the commit count since hackathon-start from git.',
    async record(s) {
      await s.frame.goto(`${BASE}__card.html?k=built`, { waitUntil: 'networkidle' });
      await s.frame.evaluate(() => document.fonts.ready);
      s.start();
      await s.hold(11000);
    },
  },
  {
    id: 'close-card',
    storyboard: '2:50–3:00 (Close)',
    xclip: null,
    speed: 1,
    desc: 'Card: the clone-and-verify command typed into a terminal line, then the repo and site URLs.',
    async record(s) {
      await s.frame.goto(`${BASE}__card.html?k=close`, { waitUntil: 'networkidle' });
      await s.frame.evaluate(() => document.fonts.ready);
      s.start();
      await s.hold(17000);
    },
  },
  {
    id: 'landing-headline',
    storyboard: '0:00–0:22 (after the title card) and 2:47–3:00',
    xclip: null,
    speed: 1,
    vw: 1120,
    desc: 'Landing page: the two-tone headline, then the "Runs on your laptop" clone section.',
    async record(s) {
      await s.go('#/');
      await s.hold(1500);
      s.start();
      s.mark('hero');
      await s.hold(7000);
      s.mark('scroll');
      await s.scroll('.closing', 2200, s.mobile ? 160 : 220);
      s.mark('closing');
      await s.hold(9000);
    },
  },
  {
    id: 'results-scatter',
    storyboard: '0:08–0:22',
    xclip: null,
    speed: 1,
    vw: 1120,
    zoom: 1.6,
    desc: 'Results page: the pool scatter (MACE S vs DFT S, hits filled), stepping through the test pools.',
    async record(s) {
      await s.go('#/results');
      await s.frame.waitForSelector('.stats');
      const col = await s.tag('The cheap simulator, honestly', 'scatter', 1);
      await s.scroll(col, 0, s.mobile ? 90 : 120);
      if (!s.mobile) await s.cam([col], 0, 20);
      await s.hold(1200);
      s.start();
      await s.hold(2500);
      const pools = s.frame.locator(`${col} [role=group][aria-label=Pool] button`);
      const n = await pools.count();
      for (let i = 1; i < Math.min(n, 5); i++) {
        await s.tap(pools.nth(i));
        s.mark(`pool${i + 1}`);
        await s.hold(2300);
      }
      if (n) await s.tap(pools.nth(0));
      s.mark('back');
      await s.hold(4500);
      if (s.mobile) {
        await s.scroll({ text: 'What the physics can and cannot see', sel: 'h2' }, 1600, 90);
        s.mark('physics');
        await s.hold(4000);
      }
    },
  },
  {
    id: 'results-arms',
    storyboard: '1:40–2:15',
    xclip: '32–40 s',
    speed: 1,
    vw: 1120,
    desc: 'Results page: arms with intervals and both baselines, discovery curves, pre-registered tests, Brier, leak probe and claims, ceilings.',
    async record(s) {
      const off = s.mobile ? 90 : 100;
      await s.go('#/results');
      await s.frame.waitForSelector('.stats');
      await s.hold(1200);
      s.start();
      await s.hold(3000);
      await s.scroll({ text: 'Hits at 40 lookups, by arm', sel: 'h2' }, 1600, off);
      s.mark('arms');
      await s.hold(s.mobile ? 5500 : 8000);
      if (s.mobile) {
        await s.scroll({ text: 'Discovery curves', sel: 'h2' }, 1400, off);
        s.mark('curves');
        await s.hold(5000);
      }
      await s.scroll({ text: 'Pre-registered tests', sel: 'h2' }, 1600, off);
      s.mark('tests');
      await s.hold(6500);
      await s.scroll({ text: 'Does it know what it knows?', sel: 'h2' }, 1600, off);
      s.mark('brier');
      await s.hold(s.mobile ? 4500 : 7000);
      if (s.mobile) {
        await s.scroll({ text: 'Vision, leakage, claims', sel: 'h2' }, 1400, off);
        s.mark('leak');
        await s.hold(5000);
      }
      await s.scroll({ text: 'Ceilings per pool', sel: 'h2' }, 1600, off);
      s.mark('ceilings');
      await s.hold(6000);
    },
  },
  {
    id: 'console-1x',
    storyboard: '0:38–0:46',
    xclip: null,
    speed: 1,
    zoom: 2.2,
    focus: true,
    desc: "Console replay at 1x: the Director's reasoning and its parallel tool calls land in the trace.",
    async record(s) {
      await s.go(consoleHash('paused=1&speed=8'));
      await s.consoleReady();
      await s.seekExact(M.start1x);
      await s.hold(800);
      s.start();
      await s.hold(700);
      await s.speed(1);
      await s.hold(500);
      await s.play();
      s.mark('play');
      await s.hold(1800);
      if (s.mobile) await s.scroll('.c-tabs', 1400, 8);
      else await s.cam(['.c-tabs'], 1400, 12, 1.9, 'bottom');
      s.mark('close');
      await s.hold(11000);
    },
  },
  {
    id: 'console-8x',
    storyboard: '0:46–1:05',
    xclip: '10–22 s',
    speed: 8,
    desc: 'Console replay at 8x (the top bar says so): proposals with P(hit), the Critic, the batch bought, sealed cells reveal.',
    async record(s) {
      const from = clampVt(M.start1x + 10 * SEC);
      const to = hasEvent(M.firstHit) ? vt(M.firstHit) + 3 * SEC : from + 200 * SEC;
      const secs = Math.max(16, Math.min(30, (to - from) / 8 / SEC));
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(from);
      await s.hold(800);
      s.start();
      await s.hold(500);
      await s.speed(8);
      await s.hold(300);
      await s.play();
      s.mark('play');
      if (s.mobile) {
        await s.hold(3000);
        await s.scroll('.c-tabs', 1400, 8);
        await s.hold(Math.max(2000, secs * SEC * 0.45));
        await s.scroll('.c-center', 1400, 8);
        await s.hold(Math.max(2000, secs * SEC * 0.4));
      } else {
        await s.hold(secs * SEC + 1500);
      }
    },
  },
  {
    id: 'critic-veto',
    storyboard: '0:58–1:05',
    xclip: null,
    speed: 1,
    zoom: 2.4,
    focus: true,
    skipIf: () => !hasEvent(M.veto) && 'run has no critique',
    desc: 'The Critic reads the render and vetoes a pick (close-up on the trace).',
    async record(s) {
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(M.veto) - 4500);
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      else await s.cam(['.c-tabs'], 0, 10, 1.9, 'bottom');
      await s.hold(1500);
      s.start();
      await s.hold(500);
      await s.play();
      s.mark('play');
      await s.hold(6500);
      s.mark('veto');
      await s.pause();
      await s.hold(5500);
    },
  },
  {
    id: 'dft-reveal',
    storyboard: '1:05–1:15',
    xclip: '3–10 s',
    speed: 1,
    zoom: 2.4,
    skipIf: () => !hasEvent(M.bestReveal) && 'run has no dft_result',
    desc: 'A batch is bought: sealed DFT cells reveal hit/miss and CO2 moves from its starting position into the pore (pose morph).',
    async record(s) {
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(M.bestReveal) - 3000);
      if (s.mobile) await s.scroll('.viewer-host', 0, 150);
      else await s.cam(['.cand-table thead', '.viewer-host'], 0, 14);
      await s.hold(1800);
      s.start();
      await s.hold(400);
      await s.play();
      s.mark('play');
      await s.frame
        .waitForFunction(() => document.querySelector('.viewer-host')?.getAttribute('data-geom') === 'morphing', null, { timeout: 15000 })
        .catch(() => undefined);
      s.mark('morph');
      if (!s.mobile) await s.cam(['.viewer-host'], 1100, 10);
      await s.frame.waitForFunction(() => document.querySelector('.viewer-host')?.getAttribute('data-geom') === 'relaxed', null, { timeout: 15000 }).catch(() => undefined);
      s.mark('relaxed');
      await s.hold(2500);
      await s.pause();
      // play the morph once more from the viewer's own "replay" control
      const replay = s.frame.getByRole('button', { name: 'replay', exact: true });
      if (await replay.count()) {
        await s.tap(replay).catch(() => undefined);
        s.mark('replay');
        await s.hold(5000);
      }
    },
  },
  {
    id: 'calibrate-oracle',
    storyboard: '1:15–1:40',
    xclip: '22–32 s',
    speed: 1,
    zoom: 2.4,
    focus: true,
    skipIf: () => !hasEvent(M.cal) && 'run has no calibration event',
    desc: "calibrate_oracle: the parity plot lands in the trace, then (seeking ahead) the Director's next line and the Critic's visual rho.",
    async record(s) {
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(M.calCall) - 1500);
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      else await s.cam(['.c-tabs'], 0, 10, 1.9, 'bottom');
      await s.hold(1500);
      s.start();
      await s.hold(400);
      await s.play();
      s.mark('play');
      await s.hold(6500);
      s.mark('plot');
      await s.hold(1500);
      for (const i of [M.calNext, M.calCrit].filter((i) => i > M.cal)) {
        await s.seekExact(vt(i) - 1000);
        s.mark(EVENTS[i].kind === 'critique' ? 'critic' : 'next');
        await s.hold(6500);
      }
      await s.pause();
      await s.hold(1500);
    },
  },
  {
    id: 'claim-check',
    storyboard: '2:15–2:30',
    xclip: null,
    speed: 1,
    zoom: 2.4,
    focus: true,
    skipIf: () => !hasEvent(M.claim) && 'run has no claimcheck event',
    desc: 'The scrubber is dragged to the end of the run; the report lands, then the claim checker: claims, verified, flagged (in red).',
    async record(s) {
      const target = vt(hasEvent(M.report) ? M.report : M.claim) - 2500;
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(Math.max(0, target - 120 * SEC));
      await s.hold(800);
      s.start();
      await s.hold(800);
      await s.seekDrag(target, 900);
      s.mark('seek');
      await s.play();
      s.mark('play');
      await s.hold(2500);
      if (s.mobile) await s.scroll('.c-tabs', 1400, 8);
      else await s.cam(['.c-tabs'], 1400, 10, 1.9, 'bottom');
      s.mark('claims');
      await s.hold(10000);
    },
  },
  {
    id: 'approval-queue',
    storyboard: '2:30–2:47',
    xclip: null,
    speed: 1,
    zoom: 2.4,
    focus: true,
    skipIf: () => !hasEvent(M.openBatch) && 'run has no proposal before a human gate',
    desc: 'The approval queue on an open batch: move with j, approve with a. Proposed mutants (unvalidated, ML only) when the run has any.',
    async record(s) {
      await s.go(consoleHash(`paused=1&speed=1&seq=${EVENTS[M.openBatch].seq}`));
      await s.consoleReady();
      await s.tab('Queue');
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      else await s.cam(['.c-tabs'], 0, 10, 1.9, 'bottom');
      await s.hold(1500);
      s.start();
      await s.hold(1500);
      await s.frame.locator('.q-list').focus();
      for (let i = 0; i < 3; i++) {
        await s.page.keyboard.press('j');
        await s.hold(1000);
      }
      s.mark('approve');
      await s.page.keyboard.press('a');
      await s.hold(4000);
      if (M.mutations) {
        await s.seekExact(TOTAL);
        await s.tab('Queue');
        s.mark('mutants');
        await s.hold(6000);
      } else {
        // no mutants in this run: the structure of the batch's current pick
        if (!s.mobile) await s.cam(['.viewer-host'], 1200, 10);
        else await s.scroll('.viewer-host', 1200, 150);
        s.mark('structure');
        await s.hold(6000);
      }
    },
  },
  {
    id: 'console-model',
    storyboard: '0:22–0:38 (stand-in until live.mov)',
    xclip: null,
    speed: 1,
    zoom: 2.4,
    focus: true,
    desc: 'Model tab of the finished run: GGUF hash, quant, llama.cpp build, tok/s, network. Used only when live.mov is missing.',
    async record(s) {
      await s.go(consoleHash(`paused=1&speed=1&seq=${EVENTS[M.last].seq}`));
      await s.consoleReady();
      await s.tab('Model');
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      await s.hold(1200);
      s.start();
      await s.hold(1500);
      if (!s.mobile) await s.cam(['.c-tabs'], 1600, 10, 1.9, 'bottom');
      s.mark('close');
      await s.hold(14000);
    },
  },
  // ---------------------------------------------------------------- explainer scenes (scenes/*.html)
  {
    id: 'humidity',
    scene: true,
    storyboard: '0:10–0:24 (The problem, first half)',
    xclip: '4–10 s',
    speed: 1,
    marks: { water: 2.0, good: 6.4 },
    desc: 'Scene: a pore with one metal site; water takes the site before CO2 (85% of 3,000 MOFs), then in the hero MOF CO2 holds on. Bars: DFT binding strength.',
  },
  {
    id: 'bottleneck',
    scene: true,
    storyboard: '0:10–0:24 (The problem, second half)',
    xclip: null,
    speed: 1,
    marks: { pool: 1.2, picks: 2.7, reveal: 5.1 },
    desc: "Scene: the atlas's 3,000 MOFs collapse to one search of 500; 40 affordable tests light up; the run's hit flashes, then the hidden good ones show.",
  },
  {
    id: 'loop',
    scene: true,
    formats: ['desktop'], // main video only
    storyboard: '0:52–1:40 (Watch it work, first ~6 s)',
    xclip: null,
    speed: 1,
    marks: {},
    desc: 'Scene: the agent loop Plan, Pick, Check, Test, Learn, four rounds; one model (Muse Glimmer 30B) behind every role; tests used 0 to 40.',
  },
  {
    id: 'dflash',
    scene: true,
    formats: ['desktop'],
    storyboard: '0:24–0:42 (What it runs on, last ~6 s)',
    xclip: null,
    speed: 1,
    marks: {},
    desc: "Scene: Meta's DFlash speculative decoding at real speed: the drafter guesses tokens, the 30B model checks them in one pass; same text, 3.0 vs 6.8 tok/s.",
  },
  {
    id: 'result',
    scene: true,
    storyboard: '1:58 (The honest result)',
    xclip: '30–40 s',
    speed: 1,
    marks: { bars: 0.8 },
    desc: 'Scene: hits at 40 tests, blind: random, the machine-learning baseline, fortyshots (accent) and the best simple method, with faint 95% intervals.',
  },
  {
    id: 'split',
    scene: true,
    formats: ['desktop'],
    storyboard: '2:16 (The principle)',
    xclip: null,
    speed: 1,
    marks: {},
    desc: "Scene: the principle card. The AI decides what to test (it sends picks); physics decides what's true (results come back checked).",
  },
];

// ------------------------------------------------------------------------------------------- main
function ffprobeDuration(file) {
  try {
    return Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]).toString().trim());
  } catch {
    return null;
  }
}

mkdirSync(OUT, { recursive: true });
if (SHOTS.some((x) => x.scene && (!ONLY.length || ONLY.includes(x.id)))) {
  // the scenes' numbers, fresh from the result files
  console.log('[broll] ' + execFileSync('python3', [join(SCENES, 'data.py')]).toString().trim());
}
const manifestPath = join(OUT, 'shots.json');
const prev = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : null;
const keepPrev = prev && prev.run_id === RUN && ONLY.length > 0;
const manifest = {
  generated: new Date().toISOString(),
  run_id: RUN,
  run_choice: why,
  run_mode: run.mode ?? null,
  site: relative(ROOT, DOCS) || '.',
  fps: 25,
  formats: Object.fromEntries(Object.entries(FORMATS).map(([k, f]) => [k, { size: f.out, viewport: [f.vw, f.vh], scale: +f.scale.toFixed(4) }])),
  note: 'trim_in: seconds to skip at the start of the file (page loading). duration: usable seconds after trim_in. marks: seconds after trim_in.',
  shots: keepPrev ? prev.shots : {},
};

const fmts = FORMAT === 'both' ? ['desktop', 'mobile'] : [FORMAT];
console.log(`[broll] run ${RUN} (${why}); ${EVENTS.length} events, replay ${(TOTAL / 1000).toFixed(0)} s; site ${DOCS}`);
const browser = await chromium.launch();
let failures = 0;
for (const shot of SHOTS) {
  if (ONLY.length && !ONLY.includes(shot.id)) continue;
  const skip = shot.skipIf?.();
  const entry = {
    storyboard: shot.storyboard,
    x_clip: shot.xclip,
    speed: shot.speed,
    desc: shot.desc,
    ...(manifest.shots[shot.id] ?? {}),
  };
  if (skip) {
    console.log(`[broll] skip ${shot.id}: ${skip}`);
    entry.skipped = skip;
    manifest.shots[shot.id] = entry;
    continue;
  }
  delete entry.skipped;
  for (const fmt of fmts) {
    const t = Date.now();
    if (shot.formats && !shot.formats.includes(fmt)) continue;
    if (shot.scene) {
      if (STILLS.length) {
        await sceneStills(browser, fmt, shot);
        continue;
      }
      const file = `${shot.id}-${FORMATS[fmt].tag}.mp4`;
      try {
        const r = await recordScene(browser, fmt, shot, join(OUT, file));
        entry[fmt] = { file, trim_in: 0, duration: +r.usable.toFixed(2), file_duration: ffprobeDuration(join(OUT, file)), marks: r.marks, size: FORMATS[fmt].out };
        if (r.errors.length) entry[fmt].page_errors = r.errors.slice(0, 5);
        console.log(`[broll] ${shot.id.padEnd(17)} ${fmt.padEnd(7)} ${file.padEnd(34)} usable ${r.usable.toFixed(1).padStart(5)} s  (${((Date.now() - t) / 1000).toFixed(0)} s wall, frame-exact)${r.errors.length ? `  page errors: ${r.errors.length}` : ''}`);
      } catch (err) {
        failures++;
        console.error(`[broll] FAILED ${shot.id} ${fmt}: ${err?.message ?? err}`);
      }
      continue;
    }
    const s = await makeShot(browser, fmt, { zoom: shot.zoom, vw: shot.vw, focus: shot.focus });
    let result;
    try {
      await shot.record(s);
      result = await s.finish();
    } catch (err) {
      failures++;
      console.error(`[broll] FAILED ${shot.id} ${fmt}: ${err?.message ?? err}`);
      try {
        await s.finish();
      } catch {
        /* already closed */
      }
      continue;
    }
    const file = `${shot.id}-${FORMATS[fmt].tag}.webm`;
    rmSync(join(OUT, file), { force: true });
    renameSync(result.video, join(OUT, file));
    entry[fmt] = {
      file,
      trim_in: +result.trimIn.toFixed(2),
      duration: +result.usable.toFixed(2),
      file_duration: ffprobeDuration(join(OUT, file)),
      marks: result.marks,
      size: FORMATS[fmt].out,
    };
    if (s.errors.length) entry[fmt].page_errors = s.errors.slice(0, 5);
    console.log(
      `[broll] ${shot.id.padEnd(17)} ${fmt.padEnd(7)} ${file.padEnd(34)} usable ${result.usable.toFixed(1).padStart(5)} s  (${((Date.now() - t) / 1000).toFixed(0)} s wall)${s.errors.length ? `  page errors: ${s.errors.length}` : ''}`,
    );
    if (VERBOSE && s.errors.length) console.log(s.errors.join('\n'));
  }
  // `duration` at the top level: the desktop clip's usable length (what the 16:9 cut uses).
  entry.file = entry.desktop?.file ?? entry.mobile?.file ?? null;
  entry.duration = entry.desktop?.duration ?? entry.mobile?.duration ?? null;
  manifest.shots[shot.id] = entry;
}
await browser.close();
server.close();
rmSync(join(OUT, '.tmp'), { recursive: true, force: true });
if (!STILLS.length) writeFileSync(manifestPath, JSON.stringify(manifest, null, 1));
if (!STILLS.length) console.log(`[broll] wrote ${relative(ROOT, manifestPath)}${failures ? `; ${failures} recording(s) failed` : ''}`);
process.exit(failures ? 1 : 0);
