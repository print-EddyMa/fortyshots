// Screenshot pages for visual review. `node scripts/shoot.mjs [base] [names...]`
import { chromium } from './pw.mjs';
import { mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = join(WEB, 'screenshots');
mkdirSync(OUT, { recursive: true });
const base = process.argv[2] ?? 'http://127.0.0.1:4173/';
const only = process.argv.slice(3);

const PAGES = [
  { name: 'landing', hash: '#/', wait: 2500, full: true },
  { name: 'console', hash: '#/console?speed=32', wait: 9000, full: false },
  { name: 'results', hash: '#/results', wait: 1500, full: true },
  { name: 'runs', hash: '#/runs', wait: 800, full: true },
  { name: 'method', hash: '#/method', wait: 800, full: true },
];
const VPS = [
  { tag: '1440', width: 1440, height: 900, dsf: 1 },
  { tag: '390', width: 390, height: 844, dsf: 2 },
];

const browser = await chromium.launch();
for (const vp of VPS) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.dsf });
  for (const p of PAGES) {
    if (only.length && !only.includes(p.name)) continue;
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(base + p.hash, { waitUntil: 'networkidle' });
    await page.waitForTimeout(p.wait);
    const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    const file = join(OUT, `${p.name}-${vp.tag}.png`);
    await page.screenshot({ path: file, fullPage: p.full || vp.tag === '390' });
    console.log(file, 'scrollWidth', sw.join('/'), errors.length ? `ERRORS: ${errors.join(' | ')}` : '');
    await page.close();
  }
  await ctx.close();
}
await browser.close();
