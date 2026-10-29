// Renders the 1200x630 social card from the built console: `node scripts/og.mjs` (after `npm run build`).
// Writes web/public/og.png (picked up by the next build) and docs/og.png.
import { createServer } from 'node:http';
import { copyFileSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from './pw.mjs';
import { serveStatic } from './serve.mjs';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = resolve(WEB, '..', 'docs');
const index = JSON.parse(readFileSync(join(DOCS, 'data/runs/index.json'), 'utf8'));
const id = (index.find((r) => r.showcase) ?? index.find((r) => r.arm === 'A5') ?? index[0]).run_id;
const server = createServer((req, res) => serveStatic(DOCS, req, res));
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.goto(`http://127.0.0.1:${server.address().port}/#/console/${id}?speed=32`, { waitUntil: 'networkidle' });
await page.waitForSelector('.console[data-ended="1"]', { timeout: 120000 });
await page.waitForTimeout(2800); // let the last morph settle
const out = join(WEB, 'public', 'og.png');
await page.screenshot({ path: out });
copyFileSync(out, join(DOCS, 'og.png'));
console.log('wrote', out);
await browser.close();
server.close();
