// Tiny static server for docs/ (no dependencies). `node scripts/serve.mjs [--port 4173] [--dir ../docs]`
import { createServer } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i >= 0 ? process.argv[i + 1] : d;
};
export const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.jsonl': 'application/x-ndjson; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8', '.ico': 'image/x-icon',
};

export function serveStatic(root, req, res) {
  const url = new URL(req.url, 'http://x');
  let p = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, '');
  let file = join(root, p);
  if (!file.startsWith(root)) return res.writeHead(403).end();
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) return res.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream', 'cache-control': 'no-cache' });
  createReadStream(file).pipe(res);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(arg('--port', 4173));
  const root = resolve(WEB, arg('--dir', '../docs'));
  createServer((req, res) => serveStatic(root, req, res)).listen(port, '127.0.0.1', () =>
    console.log(`serving ${root} at http://127.0.0.1:${port}/`),
  );
}
