// Copies run data, results and structures into the built site (docs/data/...), per CONTRACTS.md.
//
//   node scripts/copy-data.mjs            -> ../docs/data   (after vite build)
//   node scripts/copy-data.mjs --public   -> public/data    (for `vite` dev server)
//   node scripts/copy-data.mjs --out DIR  -> DIR/data       (after vite build --outDir DIR)
//
// Sources: real repo-root runs/ and results/ when present, else web/fixtures/ (mock).
// FORTYSHOTS_RUNS_DIR=<dir> replaces repo-root runs/ (demo builds and screenshots from a scratch runs dir).
// cassette.jsonl files are never copied. Prospective mode output is: runs/<id>/mutants/ (index.json and one
// viewer file per mutant with its relaxation frames) and the runs/<id>/mutations.jsonl sidecar.
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync, copyFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = resolve(WEB, '..');
const toPublic = process.argv.includes('--public');
const outIdx = process.argv.indexOf('--out'); // --out <dir>: a scratch build (e.g. vite build --outDir <dir>)
const SITE = outIdx > 0 ? resolve(process.argv[outIdx + 1]) : toPublic ? join(WEB, 'public') : join(ROOT, 'docs');
const OUT = join(SITE, 'data');
const STRUCT_LIMIT = 25 * 1024 * 1024;

const log = (...a) => console.log('[copy-data]', ...a);

function dirSize(d) {
  let n = 0;
  for (const f of readdirSync(d)) n += statSync(join(d, f)).size;
  return n;
}

function runDirs(base) {
  if (!existsSync(base)) return [];
  return readdirSync(base).filter((n) => {
    const p = join(base, n);
    return statSync(p).isDirectory() && existsSync(join(p, 'events.jsonl'));
  });
}

// Build an index entry from a run directory when runs/index.json is missing or stale.
function indexEntry(base, id) {
  const lines = readFileSync(join(base, id, 'events.jsonl'), 'utf8').split('\n').filter(Boolean);
  const first = lines.length ? JSON.parse(lines[0]) : null;
  const h = first && first.kind === 'header' ? first.payload : {};
  let summary = null;
  if (existsSync(join(base, id, 'summary.json'))) summary = JSON.parse(readFileSync(join(base, id, 'summary.json'), 'utf8'));
  let spent = 0, hits = 0;
  for (const l of lines) {
    try {
      const e = JSON.parse(l);
      if (e.kind === 'dft_result') { spent = e.payload.spent; hits = e.payload.hits_total; }
    } catch { /* partial last line while a run is being written */ }
  }
  return {
    run_id: id, arm: h.arm ?? id.replace(/^mock-/, '').split('-')[0], pool: h.pool ?? null, seed: h.seed ?? null,
    mode: h.mode ?? (id.startsWith('mock-') ? 'mock' : 'recorded'),
    hits: summary?.hits ?? hits, spent: summary?.spent ?? spent, duration_s: summary?.duration_s ?? null,
    started: first?.t ?? null, model: h.model?.name ?? null,
  };
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const manifest = { built: new Date().toISOString(), sources: {} };

// ---- runs
const realRuns = process.env.FORTYSHOTS_RUNS_DIR ? resolve(process.env.FORTYSHOTS_RUNS_DIR) : join(ROOT, 'runs');
const useReal = runDirs(realRuns).length > 0;
const runsSrc = useReal ? realRuns : join(WEB, 'fixtures', 'runs');
manifest.sources.runs = useReal ? (process.env.FORTYSHOTS_RUNS_DIR ? `${realRuns} (FORTYSHOTS_RUNS_DIR)` : 'runs/') : 'web/fixtures/runs (mock)';
mkdirSync(join(OUT, 'runs'), { recursive: true });
const ids = runDirs(runsSrc);
let nMutants = 0;
for (const id of ids) {
  cpSync(join(runsSrc, id), join(OUT, 'runs', id), {
    recursive: true,
    filter: (src) => !src.endsWith('cassette.jsonl') && !src.endsWith('.tmp'),
  });
  // prospective mode: mutants/ (index + viewer files with frames) and the mutations.jsonl sidecar
  const mdir = join(runsSrc, id, 'mutants');
  if (existsSync(join(mdir, 'index.json'))) {
    mkdirSync(join(OUT, 'runs', id, 'mutants'), { recursive: true });
    for (const f of readdirSync(mdir)) if (f.endsWith('.json')) copyFileSync(join(mdir, f), join(OUT, 'runs', id, 'mutants', f));
    nMutants += JSON.parse(readFileSync(join(mdir, 'index.json'), 'utf8')).mutants?.length ?? 0;
  }
}
let index = [];
if (existsSync(join(runsSrc, 'index.json'))) index = JSON.parse(readFileSync(join(runsSrc, 'index.json'), 'utf8'));
const known = new Set(index.map((r) => r.run_id));
for (const id of ids) if (!known.has(id)) index.push(indexEntry(runsSrc, id));
index = index.filter((r) => ids.includes(r.run_id));
writeFileSync(join(OUT, 'runs', 'index.json'), JSON.stringify(index, null, 1));
log(`runs: ${ids.length} from ${manifest.sources.runs}; prospective mutants: ${nMutants}`);

// ---- results
mkdirSync(join(OUT, 'results'), { recursive: true });
for (const f of ['results.json', 'pools.json']) {
  const real = join(ROOT, 'results', f);
  const src = existsSync(real) ? real : join(WEB, 'fixtures', 'results', f);
  manifest.sources[f] = existsSync(real) ? `results/${f}` : `web/fixtures/results/${f} (mock)`;
  copyFileSync(src, join(OUT, 'results', f));
}
// copy any extra result artifacts (figures etc.) the bench writes next to results.json
if (existsSync(join(ROOT, 'results'))) {
  for (const f of readdirSync(join(ROOT, 'results'))) {
    const p = join(ROOT, 'results', f);
    if (!['results.json', 'pools.json'].includes(f) && statSync(p).isFile() && /\.(json|png|svg|csv)$/.test(f)) copyFileSync(p, join(OUT, 'results', f));
  }
  if (existsSync(join(ROOT, 'results', 'fig'))) cpSync(join(ROOT, 'results', 'fig'), join(OUT, 'results', 'fig'), { recursive: true, filter: (s) => !/\.(parquet|pkl)$/.test(s) });
}
log('results:', manifest.sources['results.json'], '|', manifest.sources['pools.json']);

// ---- structures: all if small, else only those referenced by runs + pools
const sdir = join(ROOT, 'data', 'structures');
mkdirSync(join(OUT, 'structures'), { recursive: true });
if (existsSync(sdir)) {
  if (dirSize(sdir) < STRUCT_LIMIT) {
    cpSync(sdir, join(OUT, 'structures'), { recursive: true });
    manifest.sources.structures = 'data/structures (all)';
  } else {
    const want = new Set();
    for (const id of ids) {
      const txt = readFileSync(join(runsSrc, id, 'events.jsonl'), 'utf8');
      for (const m of txt.matchAll(/"(m[0-9a-f]{7})"/g)) want.add(m[1]);
    }
    for (const m of want) {
      const p = join(sdir, `${m}.json`);
      if (existsSync(p)) copyFileSync(p, join(OUT, 'structures', `${m}.json`));
    }
    manifest.sources.structures = `data/structures (${want.size} referenced)`;
  }
}
log('structures:', manifest.sources.structures);

writeFileSync(join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 1));

if (!toPublic) {
  writeFileSync(join(SITE, '.nojekyll'), '');
  // font + viewer licenses (fonts are self-hosted, so the OFL notice ships with them)
  const lic = join(SITE, 'licenses');
  mkdirSync(lic, { recursive: true });
  copyFileSync(join(WEB, 'node_modules/@fontsource-variable/geist/LICENSE'), join(lic, 'Geist-OFL-1.1.txt'));
  copyFileSync(join(WEB, 'node_modules/@fontsource-variable/geist-mono/LICENSE'), join(lic, 'GeistMono-OFL-1.1.txt'));
  if (existsSync(join(WEB, 'node_modules/3dmol/LICENSE'))) copyFileSync(join(WEB, 'node_modules/3dmol/LICENSE'), join(lic, '3Dmol-BSD-3-Clause.txt'));
  log('wrote .nojekyll and licenses/');
}
