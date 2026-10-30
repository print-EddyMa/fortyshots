import { useEffect, useMemo, useState } from 'react';
import { CommandPill, Page, Prelim } from '../components/Chrome';
import { DiscoveryChart, type CurveSeries } from '../components/Charts';
import { Console } from '../console/Console';
import { REPO_URL, PREREG_URL } from '../lib/config';
import { hasMockRuns, isPreliminary, loadEvents, pickShowcase } from '../lib/data';
import { ci, count, num, shortSha } from '../lib/format';
import { href } from '../lib/router';
import type { IndexEntry, Results, RunEvent } from '../lib/types';
import { useIndex, useResults } from '../lib/useData';
import { usePlayback } from '../lib/usePlayback';

const repoPath = REPO_URL.replace(/^https?:\/\//, '');

const TOUR: { key: string; label: string; caption: string; find: (e: RunEvent) => boolean }[] = [
  { key: 'plan', label: 'Plan', caption: 'The Director reads what round 1 bought, checks the cheap simulator, and changes plan.', find: (e) => e.kind === 'llm_call' && e.role === 'director' && e.round === 2 },
  { key: 'propose', label: 'Propose', caption: 'The Proposer commits to ten picks, each with a P(hit), before DFT is revealed.', find: (e) => e.kind === 'proposal' && e.round === 2 },
  { key: 'critique', label: 'Critique', caption: 'The Critic reads its own parity plot through vision and vetoes weak picks.', find: (e) => e.kind === 'llm_call' && e.role === 'critic' && e.round === 2 },
  { key: 'buy', label: 'Buy DFT', caption: 'Ten sealed DFT results open. CO2 moves to its DFT-relaxed pose.', find: (e) => e.kind === 'dft_order' && e.round === 2 },
  { key: 'calibrate', label: 'Calibrate', caption: 'Calibration on the labels it paid for: does the cheap simulator rank anything?', find: (e) => e.kind === 'calibration' && e.round === 3 },
  { key: 'report', label: 'Report', caption: 'The Reporter cites a tool call for every number. A deterministic checker flags the rest.', find: (e) => e.kind === 'report' },
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
  // Headline [H]: the A5 mean from results.json; until A5 is scored, the showcase run's own count (from the run index).
  const H = a5 ? count(a5.hits_mean) : showcase ? String(showcase.hits) : '—';

  const series: CurveSeries[] = [];
  if (a0) series.push({ key: 'A0', label: 'Random', curve: a0.curve, lo: a0.curve_lo, hi: a0.curve_hi, tone: 'faint', band: true });
  const a2 = arm('A2');
  if (a2) series.push({ key: 'A2', label: 'MACE-greedy', curve: a2.curve, tone: 'faint', dashed: true });
  if (a3) series.push({ key: 'A3', label: 'RF-UCB', curve: a3.curve, lo: a3.curve_lo, hi: a3.curve_hi, tone: 'muted', band: true });
  if (a5) series.push({ key: 'A5', label: 'Glimmer agent', curve: a5.curve, lo: a5.curve_lo, hi: a5.curve_hi, tone: 'fg', band: true, width: 1.5 });

  return (
    <Page current="/">
      <section className="wrap hero">
        <h1 className="display" aria-live="polite">
          <span>
            40 experiments. {H} hit{H === '1' ? '' : 's'}.
          </span>
          <span className="second">
            Random finds {a0 ? count(a0.hits_mean) : '—'}. The best baseline finds {best ? count(best.hits_mean) : '—'}.
          </span>
        </h1>
        <div className="hero-row">
          <p className="lede">
            fortyshots is an offline AI lab. Meta's open-weights Muse Glimmer 30B runs on a 24 GB MacBook with Wi-Fi off.
            It plans, commits to predictions, reads its own plots, and buys DFT results ten at a time.
          </p>
          <div className="hero-cta">
            <a className="btn btn-primary btn-lg" href={href(showcase ? `/console/${encodeURIComponent(showcase.run_id)}` : '/console')}>
              Watch a run <span className="arrow" aria-hidden>→</span>
            </a>
            <CommandPill cmd={`git clone ${repoPath} && ./lab setup`} />
          </div>
        </div>
        <p className="hero-fine">
          {a5
            ? `Mean hits at 40 lookups, n = ${nA5} Glimmer campaigns, 95% CI ${ci(a5.hits_ci[0], a5.hits_ci[1])}.`
            : showcase
              ? `Hits in one campaign (${showcase.run_id}); the agent arm is not scored yet. Baselines: mean of ${a0?.n_runs ?? '—'} and ${a3?.n_runs ?? '—'} runs.`
              : ''} Hits are ODAC23 DFT reference labels (Meta FAIR) · pre-registered (
          <a href={PREREG_URL}>prereg-v1 {shortSha(R?.prereg_sha)}</a>) · scored blind. <a href={href('/method')}>See the method</a>.
        </p>
        {prelim && (
          <div className="hero-prelim">
            <Prelim status={R?.status} mockRuns={hasMockRuns(index.data)} />
          </div>
        )}
      </section>

      <section className="wrap">{showcase && <Hero showcase={showcase} results={R} />}</section>

      <section className="wrap section">
        <div className="section-head">
          <h2 className="label">How it works</h2>
        </div>
        <div className="rows how">
          <div className="row">
            <span className="name">Muse Glimmer 30B</span>
            <span className="desc">Director, Proposer, Critic and Reporter. One open-weights model with Meta's DFlash drafter, on a laptop.</span>
            <span className="trail">llama.cpp</span>
          </div>
          <div className="row">
            <span className="name">MACE-MP-0</span>
            <span className="desc">Cheap physics for all 500 candidates. Noisy, and the agent is not told how noisy.</span>
            <span className="trail">precomputed</span>
          </div>
          <div className="row">
            <span className="name">ODAC23 DFT</span>
            <span className="desc">The expensive experiments: Meta FAIR's DFT labels, sealed until the agent buys them.</span>
            <span className="trail">40 lookups</span>
          </div>
          <div className="row">
            <span className="name">You</span>
            <span className="desc">Approve each batch in live mode. Recorded runs auto-approve, and the ledger says so.</span>
            <span className="trail">a · r</span>
          </div>
        </div>
      </section>

      {series.length > 0 && (
        <section className="wrap section">
          <div className="section-head">
            <h2 className="label">Hits as the budget is spent</h2>
            <a className="link-quiet label" href={href('/results')}>
              All results <span aria-hidden>→</span>
            </a>
          </div>
          <p className="section-lede">
            Mean hits after each DFT lookup, with 95% bands. A hit binds CO2 below −0.5 eV and more strongly than water.
            The base rate is {R ? num(R.base_rate * 100, 1) : '—'}%.
          </p>
          <DiscoveryChart series={series} height={300} labelWidth={150} title="Mean hits against DFT lookups for the agent and baselines" />
        </section>
      )}

      <section className="wrap section">
        <div className="closing">
          <h2 className="h-section">
            Runs on your laptop. <span className="second">Clone it and check every number.</span>
          </h2>
          <CommandPill cmd={`git clone ${repoPath}`} />
        </div>
      </section>
    </Page>
  );
}
