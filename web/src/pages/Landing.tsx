import { useEffect, useMemo, useState } from 'react';
import { CommandPill, Page, Prelim } from '../components/Chrome';
import { DiscoveryChart, type CurveSeries } from '../components/Charts';
import { Console } from '../console/Console';
import { HeroMof } from '../components/HeroMof';
import { AtlasPlayer, type AtlasSeq } from '../components/Atlas';
import { loadAtlas, type AtlasData } from '../lib/chemData';
import { derive } from '../lib/replay';
import { REPO_URL } from '../lib/config';
import { hasMockRuns, isPreliminary, loadEvents, pickShowcase } from '../lib/data';
import { ci, num } from '../lib/format';
import { href } from '../lib/router';
import type { IndexEntry, Results, RunEvent } from '../lib/types';
import { useIndex, useResults } from '../lib/useData';
import { usePlayback } from '../lib/usePlayback';

const repoPath = REPO_URL.replace(/^https?:\/\//, '');

const TOUR: { key: string; label: string; caption: string; find: (e: RunEvent) => boolean }[] = [
  { key: 'plan', label: 'Plan', caption: 'Plans the next move from what it has learned.', find: (e) => e.kind === 'llm_call' && e.role === 'director' && e.round === 2 },
  { key: 'propose', label: 'Pick', caption: 'Picks ten to test, with a confidence for each.', find: (e) => e.kind === 'proposal' && e.round === 2 },
  { key: 'critique', label: 'Check', caption: 'Looks at structures and charts. Drops weak picks.', find: (e) => e.kind === 'llm_call' && e.role === 'critic' && e.round === 2 },
  { key: 'buy', label: 'Test', caption: 'Runs the simulations. Results come back.', find: (e) => e.kind === 'dft_order' && e.round === 2 },
  { key: 'calibrate', label: 'Learn', caption: 'Checks its estimates against results, and adjusts.', find: (e) => e.kind === 'calibration' && e.round === 3 },
  { key: 'report', label: 'Report', caption: 'Reports what it found. Every number is checked.', find: (e) => e.kind === 'report' },
];

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

function Hero({ showcase, results }: { showcase: IndexEntry; results: Results | null }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [step, setStep] = useState(0);
  const [cycle, setCycle] = useState(0);
  const [auto, setAuto] = useState(() => !reducedMotion());
  const pb = usePlayback(events, { speed: 8, autoplay: true });

  useEffect(() => {
    loadEvents(showcase.run_id).then(setEvents, () => setEvents([]));
  }, [showcase.run_id]);

  const idx = useMemo(() => TOUR.map((t) => events.findIndex(t.find)), [events]);

  useEffect(() => {
    if (!events.length) return;
    const i = idx[step];
    if (i >= 0) {
      pb.seekIndex(Math.max(0, i - 1));
      pb.play();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, cycle, events.length]);

  useEffect(() => {
    if (!auto || !events.length) return;
    const t = window.setTimeout(() => setStep((s) => (s + 1) % TOUR.length), 6000);
    return () => window.clearTimeout(t);
  }, [auto, step, cycle, events.length]);

  return (
    <div className="tour">
      <div className="tour-seg">
        <div className="seg" role="tablist" aria-label="Tour of a recorded run">
          {TOUR.map((t, i) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={i === step}
              onClick={() => {
                setStep(i);
                setCycle((c) => c + 1);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        {auto && (
          <div className="tour-progress" aria-hidden>
            <i key={`${step}-${cycle}`} />
          </div>
        )}
      </div>
      <p className="tour-caption">
        {TOUR[step].caption}{' '}
        <a className="link-quiet" href={href(`/console/${encodeURIComponent(showcase.run_id)}`)}>
          Open this run →
        </a>
      </p>
      <div className="hero-frame" onPointerDown={() => setAuto(false)}>
        {events.length > 0 && <Console runId={showcase.run_id} events={events} pb={pb} results={results} embedded />}
      </div>
    </div>
  );
}

/** Where the agent looked: its 40 purchases on the map of chemical space, against seed-0 baselines on the same pool. */
function AtlasSection({ showcase }: { showcase: IndexEntry }) {
  const [atlas, setAtlas] = useState<AtlasData | null>(null);
  const [agent, setAgent] = useState<string[] | null>(null);
  useEffect(() => {
    loadAtlas().then(setAtlas, () => setAtlas(null));
    loadEvents(showcase.run_id).then(
      (ev) => {
        const st = derive(ev);
        setAgent(st.cands.filter((c) => c.order != null).sort((a, b) => a.order! - b.order!).map((c) => c.id));
      },
      () => setAgent([]),
    );
  }, [showcase.run_id]);
  if (!atlas || !agent) return null;
  const pool = showcase.pool;
  const paths = atlas.paths.by_pool[pool] ?? {};
  const mock = showcase.mode === 'mock' || showcase.run_id.startsWith('mock-');
  const seqs: AtlasSeq[] = [];
  if (agent.length) seqs.push({ key: 'agent', label: 'fortyshots', ids: agent, note: `${showcase.run_id}${mock ? ' · mock run' : ''}` });
  if (paths.A0) seqs.push({ key: 'A0', label: 'Random', ids: paths.A0, note: `Random baseline, seed ${atlas.paths.seed}, results/baselines.parquet` });
  if (paths.A2) seqs.push({ key: 'A2', label: 'Best existing', ids: paths.A2, note: `Tests the materials a quick physics estimate likes most (MACE-greedy), seed ${atlas.paths.seed}` });
  if (paths.A3) seqs.push({ key: 'A3', label: 'ML model', ids: paths.A3, note: `A random-forest model trained on results so far picks the next tests (RF-UCB), seed ${atlas.paths.seed}` });
  return (
    <section className="wrap section" id="atlas">
      <div className="section-head">
        <h2 className="label">How it searches</h2>
        <span className="small mono">{atlas.n.toLocaleString()} materials</span>
      </div>
      <p className="section-lede">
        Each dot is a material. The path is the order it tested them.
      </p>
      <AtlasPlayer atlas={atlas} pool={pool} seqs={seqs} />
    </section>
  );
}

export function Landing() {
  const results = useResults();
  const index = useIndex();
  const R = results.data;
  const showcase = index.data ? pickShowcase(index.data) : null;
  const arm = (a: string) => R?.arms.find((x) => x.arm === a);
  const a5 = arm('A5');
  const a0 = arm('A0');
  const a3 = arm('A3');
  // The strongest non-LLM arm on these pools (PREREG H1 comparator), never a hand-picked one.
  const best = R?.arms.filter((x) => x.kind === 'baseline').reduce<typeof a0>((b, x) => (!b || x.hits_mean > b.hits_mean ? x : b), undefined);
  const prelim = isPreliminary(R, index.data);
  const nA5 = a5?.n_runs;

  const series: CurveSeries[] = [];
  if (a0) series.push({ key: 'A0', label: 'Random', curve: a0.curve, lo: a0.curve_lo, hi: a0.curve_hi, tone: 'faint', band: true });
  const a2 = arm('A2');
  if (a2) series.push({ key: 'A2', label: 'Best existing', curve: a2.curve, tone: 'faint', dashed: true });
  if (a3) series.push({ key: 'A3', label: 'Machine learning', curve: a3.curve, lo: a3.curve_lo, hi: a3.curve_hi, tone: 'muted', band: true });
  if (a5) series.push({ key: 'A5', label: 'fortyshots', curve: a5.curve, lo: a5.curve_lo, hi: a5.curve_hi, tone: 'fg', band: true, width: 1.5 });

  return (
    <Page current="/">
      <section className="wrap hero">
        <h1 className="display">
          <span>Find any material.</span>
          <span className="second">Describe it. An AI scientist runs the search.</span>
        </h1>
        <div className="hero-row">
          <p className="lede">
            fortyshots decides which experiments to run, learns from every result, and shows its reasoning.
          </p>
          <div className="hero-cta">
            <a className="btn btn-primary btn-lg" href={href(showcase ? `/console/${encodeURIComponent(showcase.run_id)}` : '/console')}>
              Watch it work <span className="arrow" aria-hidden>→</span>
            </a>
            <a className="btn btn-secondary btn-lg" href={href('/results')}>
              Results
            </a>
          </div>
        </div>
        {prelim && (
          <div className="hero-prelim">
            <Prelim status={R?.status} mockRuns={hasMockRuns(index.data)} />
          </div>
        )}
      </section>

      <section className="wrap">
        <HeroMof />
        <p className="small muted fig-note">
          A real find: CO2 (orange) held inside a zinc framework.
        </p>
      </section>

      <section className="wrap section">
        <div className="steps3">
          <div className="step">
            <span className="step-title">Describe it.</span>
            <p>Say what the material must do. First target: capturing CO2 from humid air.</p>
          </div>
          <div className="step">
            <span className="step-title">It searches.</span>
            <p>It reasons across hundreds of candidates and picks the most promising.</p>
          </div>
          <div className="step">
            <span className="step-title">It tests and learns.</span>
            <p>Accurate simulations check each pick. Every result sharpens the next.</p>
          </div>
        </div>
      </section>

      <section className="wrap section">
        <div className="section-head">
          <h2 className="label">Results</h2>
          <a className="link-quiet label" href={href('/results')}>
            Full results <span aria-hidden>→</span>
          </a>
        </div>
        <div className="answer">
          <div className="answer-row">
            <span className="answer-label">Random picks</span>
            <span className="answer-val mono">{a0 ? num(a0.hits_mean, 1) : '—'}</span>
          </div>
          <div className="answer-row">
            <span className="answer-label">Best existing method</span>
            <span className="answer-val mono">{best ? num(best.hits_mean, 1) : '—'}</span>
          </div>
          <div className="answer-row answer-us">
            <span className="answer-label">fortyshots</span>
            <span className="answer-val mono">{a5 ? num(a5.hits_mean, 1) : '—'}</span>
          </div>
        </div>
        <p className="small muted">
          Good materials found in 40 tests among 500 candidates. Scored blind.
          {a5 ? ` ${nA5} runs, 95% interval ${ci(a5.hits_ci[0], a5.hits_ci[1])}.` : ' fortyshots runs are being recorded.'}{' '}
          <a href={href('/method')}>Method</a>
        </p>
      </section>

      {showcase && <AtlasSection showcase={showcase} />}

      <section className="wrap section">
        <div className="section-head">
          <h2 className="label">Watch it work</h2>
        </div>
        {showcase && <Hero showcase={showcase} results={R} />}
      </section>

      <section className="wrap section">
        <div className="section-head">
          <h2 className="label">How it works</h2>
        </div>
        <div className="rows how">
          <div className="row">
            <span className="name">Model</span>
            <span className="desc">Meta Muse Glimmer 30B. Plans, picks, critiques and reports.</span>
            <span className="trail">llama.cpp</span>
          </div>
          <div className="row">
            <span className="name">Estimates</span>
            <span className="desc">Fast physics on every candidate. Cheap, often wrong.</span>
            <span className="trail">precomputed</span>
          </div>
          <div className="row">
            <span className="name">Simulations</span>
            <span className="desc">Accurate quantum chemistry from Meta&apos;s Open DAC data. 40 per search.</span>
            <span className="trail">40 lookups</span>
          </div>
          <div className="row">
            <span className="name">You</span>
            <span className="desc">Approve each batch. Choose which new materials to make.</span>
            <span className="trail">a · r</span>
          </div>
        </div>
      </section>

      {series.length > 0 && (
        <section className="wrap section">
          <div className="section-head">
            <h2 className="label">Progress per test</h2>
            <a className="link-quiet label" href={href('/results')}>
              All results <span aria-hidden>→</span>
            </a>
          </div>
          <p className="section-lede">
            Good materials found as tests are spent. {R ? num(R.base_rate * 100, 1) : '—'}% of candidates qualify.
          </p>
          <DiscoveryChart series={series} height={300} labelWidth={150} title="Mean hits against DFT lookups for the agent and baselines" />
        </section>
      )}

      <section className="wrap section">
        <div className="closing">
          <h2 className="h-section">
            The AI decides what to test. <span className="second">Physics decides what&apos;s true.</span>
          </h2>
          <CommandPill cmd={`git clone ${repoPath}`} />
        </div>
      </section>
    </Page>
  );
}
