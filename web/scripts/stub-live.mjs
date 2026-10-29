// Stub of `fortyshots serve` for testing live mode (?live=1) without the agent or Python.
//   node scripts/stub-live.mjs [--port 7777] [--run <run_id>] [--rate 20]
// Mirrors fortyshots/server.py (CONTRACTS addenda):
//   GET  /api/runs                       index
//   GET  /api/runs/<id>/events           SSE, `event: event` + `id: <seq>` per ledger line, `event: end` after the summary
//   GET  /api/runs/<id>/pending          {round, ids} while the run waits on the human gate, else {}
//   POST /api/runs/<id>/human            {action, ids, note, round?}  (one decision per round)
//   GET  /api/runs/<id>/summary, /api/runs/<id>/img/<name>, /api/network
//   static: docs/ at /, runs at /data/runs/
// The stub replays a recorded run. At each human-gate event it stops, publishes pending.json, and waits for a POST;
// the decision then replaces the recorded `auto` event (rejected picks are not actually replaced by the stub).
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { serveStatic } from './serve.mjs';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = resolve(WEB, '..');
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i >= 0 ? process.argv[i + 1] : d;
};
const port = Number(arg('--port', 7777));
const rate = Number(arg('--rate', 20));
const site = resolve(ROOT, 'docs');
const runsDir = existsSync(join(site, 'data', 'runs', 'index.json')) ? join(site, 'data', 'runs') : join(WEB, 'fixtures', 'runs');
const index = JSON.parse(readFileSync(join(runsDir, 'index.json'), 'utf8'));
const liveId = arg('--run', (index.find((r) => r.showcase) ?? index[0]).run_id);
const streams = new Map();
const humanLog = [];

function stream(id) {
  if (streams.has(id)) return streams.get(id);
  const p = join(runsDir, id, 'events.jsonl');
  const all = existsSync(p) ? readFileSync(p, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  if (all[0]?.kind === 'header') all[0] = { ...all[0], payload: { ...all[0].payload, mode: 'live' } };
  const s = { pending: all, sent: [], clients: new Set(), gate: null, decision: null };
  streams.set(id, s);
  const push = (e) => {
    s.sent.push(e);
    for (const c of s.clients) send(c, e);
  };
  const timer = setInterval(() => {
    if (s.gate) {
      if (!s.decision) return;
      const e = s.pending.shift();
      const d = s.decision;
      push({ ...e, payload: { action: d.action, ids: d.action === 'reject' ? d.ids : e.payload.ids, note: d.note ?? '' } });
      s.gate = null;
      s.decision = null;
      return;
    }
    if (!s.pending.length) return clearInterval(timer);
    const e = s.pending[0];
    if (e.kind === 'human') {
      s.gate = { round: e.round, ids: e.payload.ids };
      return;
    }
    push(s.pending.shift());
  }, 1000 / rate);
  return s;
}

function send(res, e) {
  res.write(`id: ${e.seq}\nevent: event\ndata: ${JSON.stringify(e)}\n\n`);
  if (e.kind === 'summary') res.write('event: end\ndata: {}\n\n');
}

const json = (res, code, body) => {
  res.writeHead(code, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/api/runs') return json(res, 200, index.map((r) => (r.run_id === liveId ? { ...r, mode: 'live' } : r)));
  if (url.pathname === '/api/network') return json(res, 200, { network: 'off' });
  if (url.pathname === '/api/_human_log') return json(res, 200, humanLog);
  const m = url.pathname.match(/^\/api\/runs\/([^/]+)\/(events|human|pending|summary|img)(?:\/(.+))?$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    const s = stream(id);
    if (m[2] === 'events') {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
      for (const e of s.sent) send(res, e);
      s.clients.add(res);
      const ka = setInterval(() => res.write(': ping\n\n'), 15000);
      req.on('close', () => {
        clearInterval(ka);
        s.clients.delete(res);
      });
      return;
    }
    if (m[2] === 'pending') return json(res, 200, s.gate ?? {});
    if (m[2] === 'summary') {
      const e = s.sent.find((x) => x.kind === 'summary');
      return e ? json(res, 200, e.payload) : json(res, 404, { detail: 'run not finished' });
    }
    if (m[2] === 'img') {
      req.url = `/${id}/img/${m[3]}`;
      return serveStatic(runsDir, req, res);
    }
    if (m[2] === 'human' && req.method === 'POST') {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        let p;
        try {
          p = JSON.parse(body);
        } catch {
          return json(res, 400, { detail: 'bad json' });
        }
        if (!['approve', 'reject'].includes(p.action)) return json(res, 422, { detail: 'action must be approve or reject' });
        const rnd = p.round ?? s.gate?.round;
        if (rnd == null) return json(res, 409, { detail: 'no batch is waiting for approval' });
        const entry = { round: rnd, action: p.action, ids: p.ids ?? [], note: String(p.note ?? '').slice(0, 500) };
        humanLog.push(entry);
        if (s.gate && s.gate.round === rnd && !s.decision) s.decision = entry;
        console.log('[stub] human', JSON.stringify(entry));
        json(res, 200, { ok: true, ...entry });
      });
      return;
    }
  }
  if (url.pathname.startsWith('/data/runs/')) {
    req.url = url.pathname.replace('/data/runs/', '/');
    return serveStatic(runsDir, req, res);
  }
  serveStatic(site, req, res);
}).listen(port, '127.0.0.1', () => console.log(`live stub at http://127.0.0.1:${port}/?live=1#/console/${liveId}`));
