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
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
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

const server = createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/__broll.html') {
    const f = FORMATS[u.searchParams.get('f')] ?? FORMATS.desktop;
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' });
    return res.end(wrapperHtml(f));
  }
  serveStatic(DOCS, req, res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// ------------------------------------------------------------------------------------------- shot context
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function makeShot(browser, fmt, opts = {}) {
  const f = FORMATS[fmt];
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
  await page.goto(`${BASE}__broll.html?f=${fmt}`);
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
      await frame.evaluate(() => document.fonts.ready);
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
    async cam(sels, ms = 1200, pad = 16, maxZoom = zoomMax) {
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
      const steps = Math.max(8, Math.round(dur / 40));
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

// ------------------------------------------------------------------------------------------- shots
const consoleHash = (q = '') => `#/console/${encodeURIComponent(RUN)}${q ? `?${q}` : ''}`;
const hasEvent = (i) => i >= 0;
const SEC = 1000;

const SHOTS = [
  {
    id: 'landing-headline',
    storyboard: '2:47–3:00',
    xclip: null,
    speed: 1,
    desc: 'Landing page: the two-tone headline, then the "Runs on your laptop" clone section.',
    async record(s) {
      await s.go('#/');
      await s.hold(1500);
      s.start();
      s.mark('hero');
      await s.hold(6000);
      s.mark('scroll');
      await s.scroll('.closing', 2200, s.mobile ? 120 : 260);
      s.mark('closing');
      await s.hold(8000);
    },
  },
  {
    id: 'results-scatter',
    storyboard: '0:08–0:22',
    xclip: null,
    speed: 1,
    desc: 'Results page: the pool scatter (MACE S vs DFT S, hits filled), stepping through the test pools.',
    async record(s) {
      await s.go('#/results');
      await s.frame.waitForSelector('.stats');
      await s.scroll({ text: 'The cheap simulator, honestly', sel: 'h2' }, 0, 40);
      await s.hold(1200);
      s.start();
      await s.hold(2500);
      const pools = s.frame.getByRole('group', { name: 'Pool' }).getByRole('button');
      const n = await pools.count();
      for (let i = 1; i < Math.min(n, 4); i++) {
        await pools.nth(i).click();
        s.mark(`pool${i + 1}`);
        await s.hold(2200);
      }
      if (n) await pools.nth(0).click();
      s.mark('back');
      await s.hold(4000);
      if (s.mobile) {
        await s.scroll({ text: 'What the physics can and cannot see', sel: 'h2' }, 1600, 40);
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
    desc: 'Results page: arms with intervals and both baselines, discovery curves, pre-registered tests, Brier, leak probe and claims.',
    async record(s) {
      await s.go('#/results');
      await s.frame.waitForSelector('.stats');
      await s.hold(1200);
      s.start();
      await s.hold(2500);
      await s.scroll({ text: 'Hits at 40 lookups, by arm', sel: 'h2' }, 1600, 48);
      s.mark('arms');
      await s.hold(s.mobile ? 5000 : 7000);
      if (s.mobile) {
        await s.scroll({ text: 'Discovery curves', sel: 'h2' }, 1400, 48);
        s.mark('curves');
        await s.hold(5000);
      }
      await s.scroll({ text: 'Pre-registered tests', sel: 'h2' }, 1600, 48);
      s.mark('tests');
      await s.hold(6000);
      await s.scroll({ text: 'Does it know what it knows?', sel: 'h2' }, 1600, 48);
      s.mark('brier');
      await s.hold(s.mobile ? 4000 : 7000);
      if (s.mobile) {
        await s.scroll({ text: 'Vision, leakage, claims', sel: 'h2' }, 1400, 48);
        s.mark('leak');
        await s.hold(5000);
      }
    },
  },
  {
    id: 'console-1x',
    storyboard: '0:38–0:46',
    xclip: null,
    speed: 1,
    zoom: 2.2,
    desc: "Console replay at 1x: the Director's parallel tool calls land in the trace.",
    async record(s) {
      await s.go(consoleHash('paused=1&speed=8'));
      await s.consoleReady();
      await s.seekExact(M.start1x);
      await s.hold(800);
      s.start();
      await s.hold(600);
      await s.speed(1);
      await s.hold(400);
      await s.play();
      s.mark('play');
      await s.hold(1800);
      if (s.mobile) await s.scroll('.c-tabs', 1400, 8);
      else await s.cam(['.c-tabs'], 1400, 12);
      s.mark('close');
      await s.hold(10500);
    },
  },
  {
    id: 'console-8x',
    storyboard: '0:46–1:05',
    xclip: '10–22 s',
    speed: 8,
    desc: 'Console replay at 8x (label visible in the top bar): proposals with P(hit), the Critic, the batch bought, sealed cells reveal.',
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
    zoom: 3,
    skipIf: () => !hasEvent(M.veto) && 'run has no critique',
    desc: 'The Critic reads the render and vetoes a pick (close-up on the trace), then the vetoed row in the table.',
    async record(s) {
      const c = EVENTS[M.veto];
      const vetoId = c.payload?.vetoes?.[0]?.id;
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(M.veto) - 4500);
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      else await s.cam(['.c-tabs'], 0, 10);
      await s.hold(1500);
      s.start();
      await s.hold(500);
      await s.play();
      s.mark('play');
      await s.hold(6500);
      s.mark('veto');
      await s.pause();
      await s.hold(1500);
      if (vetoId) {
        const row = s.frame.locator('.cand-table tbody tr', { hasText: vetoId }).first();
        if (s.mobile) {
          await s.scroll('.c-center', 1400, 8);
          await row.scrollIntoViewIfNeeded().catch(() => undefined);
        } else {
          await s.cam(['.cand-table'], 1200, 8, 1.6);
        }
        await s.tap(row).catch(() => undefined);
        s.mark('row');
        await s.hold(3500);
      }
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
    zoom: 3,
    skipIf: () => !hasEvent(M.cal) && 'run has no calibration event',
    desc: "calibrate_oracle: the parity plot appears in the trace, the Director's next line, then the Critic's visual rho (scrubber seeks between them).",
    async record(s) {
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(M.calCall) - 1500);
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      await s.hold(1500);
      s.start();
      await s.hold(400);
      await s.play();
      s.mark('play');
      if (!s.mobile) await s.cam(['.c-tabs'], 1400, 10);
      await s.hold(5600);
      s.mark('plot');
      const later = [M.calNext, M.calCrit].filter((i) => i > M.cal);
      for (const i of later) {
        // the scrubber is in the top bar: step out of the close-up, drag it, step back in
        if (!s.mobile) await s.cam(null, 700);
        else await s.scroll(0, 900, 0);
        await s.pause();
        await s.seekDrag(vt(i) - 1200);
        await s.play();
        if (!s.mobile) await s.cam(['.c-tabs'], 900, 10);
        else await s.scroll('.c-tabs', 900, 8);
        s.mark(EVENTS[i].kind === 'critique' ? 'critic' : 'next');
        await s.hold(5500);
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
    zoom: 3,
    skipIf: () => !hasEvent(M.claim) && 'run has no claimcheck event',
    desc: 'The report lands, then the claim checker: claims, verified, flagged (in red).',
    async record(s) {
      await s.go(consoleHash('paused=1&speed=1'));
      await s.consoleReady();
      await s.seekExact(vt(hasEvent(M.report) ? M.report : M.claim) - 2500);
      await s.hold(800);
      s.start();
      await s.hold(500);
      await s.play();
      s.mark('play');
      await s.hold(3500);
      if (s.mobile) await s.scroll('.c-tabs', 1400, 8);
      else await s.cam(['.c-tabs'], 1400, 10);
      s.mark('claims');
      await s.hold(9000);
    },
  },
  {
    id: 'approval-queue',
    storyboard: '2:30–2:47',
    xclip: null,
    speed: 1,
    zoom: 3,
    skipIf: () => !hasEvent(M.openBatch) && 'run has no proposal before a human gate',
    desc: 'The approval queue on an open batch: move with j, approve with a. Proposed mutants (unvalidated, ML only) when the run has any.',
    async record(s) {
      await s.go(consoleHash(`paused=1&speed=1&seq=${EVENTS[M.openBatch].seq}`));
      await s.consoleReady();
      await s.tab('Queue');
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      else await s.cam(['.c-tabs'], 0, 10);
      await s.hold(1500);
      s.start();
      await s.hold(1500);
      await s.frame.locator('.q-list').focus();
      for (let i = 0; i < 3; i++) {
        await s.page.keyboard.press('j');
        await s.hold(900);
      }
      s.mark('approve');
      await s.page.keyboard.press('a');
      await s.hold(3500);
      if (M.mutations) {
        if (!s.mobile) await s.cam(null, 700);
        await s.seekDrag(TOTAL);
        await s.tab('Queue');
        if (!s.mobile) await s.cam(['.c-tabs'], 900, 10);
        s.mark('mutants');
        await s.hold(6000);
      } else {
        // no mutants in this run: show the structure of the approved batch's first pick
        if (!s.mobile) await s.cam(['.viewer-host'], 1200, 10, 2.2);
        else await s.scroll('.viewer-host', 1200, 150);
        s.mark('structure');
        await s.hold(5000);
      }
    },
  },
  {
    id: 'console-model',
    storyboard: '0:22–0:38 (stand-in until live.mov)',
    xclip: null,
    speed: 1,
    zoom: 3,
    desc: 'Model tab of the finished run: GGUF hash, quant, llama.cpp build, tok/s, network. Used only when live.mov is missing.',
    async record(s) {
      await s.go(consoleHash(`paused=1&speed=1&seq=${EVENTS[M.last].seq}`));
      await s.consoleReady();
      await s.tab('Model');
      if (s.mobile) await s.scroll('.c-tabs', 0, 8);
      await s.hold(1200);
      s.start();
      await s.hold(1500);
      if (!s.mobile) await s.cam(['.c-tabs'], 1600, 10);
      s.mark('close');
      await s.hold(12000);
    },
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
    const s = await makeShot(browser, fmt, { zoom: shot.zoom });
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
writeFileSync(manifestPath, JSON.stringify(manifest, null, 1));
console.log(`[broll] wrote ${relative(ROOT, manifestPath)}${failures ? `; ${failures} recording(s) failed` : ''}`);
process.exit(failures ? 1 : 0);
