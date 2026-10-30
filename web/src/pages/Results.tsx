import { useEffect, useMemo, useState } from 'react';
import { HitWord, Page, Prelim, Stat } from '../components/Chrome';
import { ArmsChart, DiscoveryChart, ReliabilityChart, ScatterChart, type CurveSeries } from '../components/Charts';
import { curveOf, meanHits, pick, type BaselineArm } from '../lib/baselines';
import { hasMockRuns, isPreliminary, loadEvents } from '../lib/data';
import { ARM_LABEL, ci, count, num, shortSha } from '../lib/format';
import { href } from '../lib/router';
import { derive } from '../lib/replay';
import type { IndexEntry, PoolRow, Pools, Results, RunEvent } from '../lib/types';
import { useIndex, usePools, useResults } from '../lib/useData';
import { PREREG_URL } from '../lib/config';
import { PeriodicTable } from '../components/PeriodicTable';

const S = (co2: number, h2o: number) => co2 + Math.max(0, co2 - h2o + 0.1);

function Reroll({ pools, results }: { pools: Pools; results: Results }) {
  const testPools = results.pools.map((p) => p.pool).filter((p) => pools.pools[p]);
  const [pool, setPool] = useState(testPools[0] ?? 'P1');
  const [seedText, setSeedText] = useState('0');
  const [out, setOut] = useState<{ arm: BaselineArm; picks: PoolRow[]; mean: number }[] | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const seed = Number.parseInt(seedText, 10);
  const valid = Number.isFinite(seed) && seed >= 0 && seed < 2 ** 31;

  const run = () => {
    if (!valid) return;
    const rows = pools.pools[pool] ?? [];
    const t0 = performance.now();
    const res = (['A0', 'A1', 'A2'] as BaselineArm[]).map((arm) => ({ arm, picks: pick(rows, arm, seed, results.budget), mean: meanHits(rows, arm, 200, results.budget) }));
    setMs(performance.now() - t0);
    setOut(res);
  };
  useEffect(run, [pool]); // eslint-disable-line react-hooks/exhaustive-deps

  const hitsInPool = (pools.pools[pool] ?? []).filter((r) => r.hit).length;
  const series: CurveSeries[] = (out ?? []).map((o) => ({
    key: o.arm,
    label: ARM_LABEL[o.arm],
    curve: curveOf(o.picks),
    tone: o.arm === 'A0' ? 'faint' : o.arm === 'A1' ? 'muted' : 'fg',
    step: true,
    digits: 0,
    dashed: o.arm === 'A2',
  }));

  return (
    <div className="reroll">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <div className="field">
          <span className="label" id="pool-label">
            Pool
          </span>
          <div className="seg seg-sm" role="group" aria-labelledby="pool-label">
            {testPools.map((p) => (
              <button key={p} type="button" aria-pressed={p === pool} onClick={() => setPool(p)}>
                {p}
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <label htmlFor="seed">Seed</label>
          <input id="seed" className="input" inputMode="numeric" value={seedText} onChange={(e) => setSeedText(e.target.value.replace(/[^0-9]/g, ''))} aria-invalid={!valid} />
        </div>
        <button type="submit" className="btn btn-secondary btn-sm" disabled={!valid}>
          Re-roll
        </button>
        <p className="small" style={{ marginTop: 16 }}>
          {pools.pools[pool]?.length ?? 0} MOFs, {hitsInPool} hits in {pool}. Runs in your browser on results/pools.json
          {ms != null ? <span className="mono"> · {ms.toFixed(0)} ms</span> : ''}. Same rules as bench/arms.py, but the browser's random stream is not numpy's, so seed N here is not seed N there. A3 needs a random forest and is not re-rolled here.
        </p>
      </form>
      <div className="reroll-out">
        <div className="rows">
          {(out ?? []).map((o) => {
            const hits = o.picks.filter((p) => p.hit);
            return (
              <div className="row" key={o.arm}>
                <span>
                  {ARM_LABEL[o.arm]} <span className="faint mono">{o.arm}</span>
                </span>
                <span className="desc">
                  <span className="ids">{hits.length ? hits.map((h) => h.id).join(' ') : 'no hits'}</span>
                </span>
                <span className="trail">
                  <span className="num-big">{hits.length}</span>
                  <span className="faint mono"> / {o.picks.length} · mean {num(o.mean, 1)}</span>
                </span>
              </div>
            );
          })}
        </div>
        {series.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <DiscoveryChart series={series} height={200} labelWidth={130} title="Hits against lookups for this seed" />
          </div>
        )}
      </div>
    </div>
  );
}

function HitsAndMisses({ index, pools }: { index: IndexEntry[]; pools: Pools }) {
  const a5 = index.filter((r) => r.arm === 'A5');
  const [runId, setRunId] = useState(a5.find((r) => r.showcase)?.run_id ?? a5[0]?.run_id);
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  useEffect(() => {
    if (!runId) return;
    setEvents(null);
    loadEvents(runId).then(setEvents, () => setEvents([]));
  }, [runId]);
  const entry = a5.find((r) => r.run_id === runId);
  const st = useMemo(() => (events ? derive(events) : null), [events]);
  if (!a5.length || !entry) return <p className="muted">No agent runs yet.</p>;
  const poolRows = pools.pools[entry.pool] ?? [];
  const bought = new Set(st?.cands.filter((c) => c.order != null).map((c) => c.id));
  const vetoes = new Map<string, string>();
  for (const e of events ?? []) if (e.kind === 'critique') for (const v of e.payload.vetoes ?? []) vetoes.set(v.id, v.reason);
  const found = (st?.cands ?? []).filter((c) => c.hit).sort((a, b) => (a.S ?? 0) - (b.S ?? 0));
  const missed = poolRows
    .filter((r) => r.hit && !bought.has(r.id))
    .map((r) => {
      const seen = st?.rowsSource[r.id];
      const reason = vetoes.has(r.id)
        ? `vetoed by the Critic: ${vetoes.get(r.id)}`
        : seen
          ? `surfaced in ${seen}, not picked`
          : 'never surfaced by a query';
      return { r, reason, S: S(r.E_CO2, r.E_H2O) };
    })
    .sort((a, b) => a.S - b.S);
  return (
    <>
      <div className="filters">
        <div className="seg seg-sm" role="group" aria-label="Agent run">
          {a5.map((r) => (
            <button key={r.run_id} type="button" aria-pressed={r.run_id === runId} onClick={() => setRunId(r.run_id)}>
              {r.pool} · s{r.seed}
            </button>
          ))}
        </div>
        <a className="link-quiet small mono" href={href(`/console/${encodeURIComponent(entry.run_id)}`)}>
          {entry.run_id} ↗
        </a>
      </div>
      <div className="split">
        <div>
          <h3 className="fig-title">Found</h3>
          <p className="fig-sub">{found.length} hits bought, strongest first.</p>
          <div className="table-scroll" tabIndex={0}>
            <table className="data">
              <thead>
                <tr>
                  <th>ID</th>
                  <th className="r">Lookup</th>
                  <th className="r">E_CO2 (eV)</th>
                  <th className="r">E_H2O (eV)</th>
                  <th className="r">P(hit)</th>
                  <th>Hit</th>
                </tr>
              </thead>
              <tbody>
                {found.map((c) => (
                  <tr key={c.id}>
                    <td className="n">{c.id}</td>
                    <td className="n r">{c.order}</td>
                    <td className="n r">{num(c.E_CO2, 2)}</td>
                    <td className="n r">{num(c.E_H2O, 2)}</td>
                    <td className="n r">{num(c.p_hit, 2)}</td>
                    <td>
                      <HitWord hit={c.hit} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3 className="fig-title">Missed</h3>
          <p className="fig-sub">
            {missed.length} hits in {entry.pool} the agent did not buy, and why.
          </p>
          <div className="table-scroll" tabIndex={0}>
            <table className="data">
              <thead>
                <tr>
                  <th>ID</th>
                  <th className="r">E_CO2 (eV)</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {missed.map(({ r, reason }) => (
                  <tr key={r.id}>
                    <td className="n">{r.id}</td>
                    <td className="n r">{num(r.E_CO2, 2)}</td>
                    <td className="missed-reason">{reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

export function ResultsPage() {
  const results = useResults();
  const pools = usePools();
  const index = useIndex();
  const R = results.data;
  const [parityPool, setParityPool] = useState<string | null>(null);

  if (!R) {
    return (
      <Page current="/results">
        <section className="wrap page-head">
          <p className="muted">{results.error ? `Could not load results.json: ${results.error}` : 'Loading results…'}</p>
        </section>
      </Page>
    );
  }
  const arm = (a: string) => R.arms.find((x) => x.arm === a);
  const a5 = arm('A5');
  const a3 = arm('A3');
  const a2 = arm('A2');
  const a0 = arm('A0');
  const a5runs = (index.data ?? []).filter((r) => r.arm === 'A5' && r.duration_s != null);
  const minutes = a5runs.length ? a5runs.reduce((s, r) => s + (r.duration_s ?? 0), 0) / a5runs.length / 60 : null;
  const prelim = isPreliminary(R, index.data);
  const order = ['A5', 'A6', 'A4', 'A3', 'A2', 'A1', 'A0'];
  const armsSorted = [...R.arms].sort((a, b) => order.indexOf(a.arm) - order.indexOf(b.arm));
  const pp = parityPool ?? R.pools[0]?.pool ?? 'P1';
  const parityRows = pools.data?.pools[pp] ?? [];

  const curves: CurveSeries[] = armsSorted
    .slice()
    .reverse()
    .map((a) => ({
      key: a.arm,
      label: ARM_LABEL[a.arm] ?? a.label,
      curve: a.curve,
      lo: a.arm === 'A5' || a.arm === 'A3' || a.arm === 'A0' ? a.curve_lo : undefined,
      hi: a.arm === 'A5' || a.arm === 'A3' || a.arm === 'A0' ? a.curve_hi : undefined,
      band: a.arm === 'A5' || a.arm === 'A0',
      tone: a.arm === 'A5' ? 'fg' : a.arm === 'A3' || a.arm === 'A6' ? 'muted' : 'faint',
      width: a.arm === 'A5' ? 1.5 : 1,
      dashed: a.kind === 'baseline' && a.arm !== 'A0',
    }));

  return (
    <Page current="/results">
      <section className="wrap page-head">
        <h1 className="h-section">
          <span>Does it work?</span>
          <span className="second">A fair test, planned in advance and scored blind.</span>
        </h1>
        <p className="lede">
          Each method gets 40 tests to find good CO2-capture materials among 500 candidates, about 14 of which are good. We
          compare fortyshots with random picking and with simpler methods, on five separate sets. Every number here can be
          recomputed from the published run logs with one command (<span className="mono">./lab verify</span>), no AI model needed.
        </p>
        {prelim && <Prelim status={R.status} mockRuns={hasMockRuns(index.data)} />}
      </section>

      <section className="wrap">
        <div className="stats">
          <Stat label="fortyshots: good materials found in 40 tests" value={num(a5?.hits_mean, 1)} unit="/ 40" sub={a5 ? `95% CI ${ci(a5.hits_ci[0], a5.hits_ci[1])}, n = ${a5.n_runs} runs` : undefined} />
          <Stat label="Times better than random" value={num(a5?.enrichment, 1)} unit="×" sub={`base rate ${num(R.base_rate * 100, 1)}%`} />
          <Stat label="Best simple method (MACE-greedy)" value={num(a2?.hits_mean, 1)} unit="/ 40" sub={a2 ? `machine-learning method (RF-UCB): ${num(a3?.hits_mean, 2)}` : undefined} />
          <Stat label="Random picking" value={num(a0?.hits_mean, 1)} unit="/ 40" sub={a0 ? `95% CI ${ci(a0.hits_ci[0], a0.hits_ci[1])}, n = ${a0.n_runs}` : undefined} />
          <Stat label="Are its confidence levels honest?" value={num(R.calibration?.brier, 3)} sub={R.calibration ? `Brier score over ${R.calibration.n} predictions; lower is better` : 'not available yet'} />
          <Stat label="Claims in its reports that check out" value={R.claims ? R.claims.verified : '—'} unit={R.claims ? `/ ${R.claims.n}` : undefined} sub={R.claims ? `${R.claims.flagged} flagged by the claim checker` : undefined} />
          <Stat label="Tests until its first good material" value={num(a5?.first_hit_mean, 1)} sub={a0 ? `random: ${num(a0.first_hit_mean, 1)}` : undefined} />
          <Stat label="Minutes per search, on a laptop" value={num(minutes, 1)} unit="min" sub={a5runs.length ? `mean of ${a5runs.length} runs on the laptop` : undefined} />
        </div>
      </section>

      <section className="wrap section">
        <h2 className="fig-title" id="why-hard">Why this is hard</h2>
        {pools.data ? (
          (() => {
            const rows = Object.values(pools.data.pools).flat();
            const hits = rows.filter((r) => r.hit).length;
            const waterWins = rows.filter((r) => r.E_H2O < r.E_CO2).length / (rows.length || 1);
            return (
              <>
              <p className="fig-sub">
                DFT binding energy of CO2 against water for every MOF in the six pools. Water binds more strongly than CO2 in{' '}
                <span className="callout mono">{Math.round(waterWins * 100)}%</span> of them (above the dashed line), which is why sorbents fail
                in humid air. Filled: hits, which bind CO2 below −0.5 eV and more strongly than water.
              </p>
              <ScatterChart
                points={rows.map((r) => ({ x: r.E_H2O, y: r.E_CO2, strong: r.hit, id: r.id }))}
                xLabel="E water (eV)"
                yLabel="E CO2 (eV)"
                height={360}
                ariaLabel={`CO2 versus water binding energy for ${rows.length} MOFs, ${hits} hits`}
                annotation={`n = ${rows.length.toLocaleString()} MOFs · ${hits} hits (${num((100 * hits) / rows.length, 1)}%) · water binds more strongly in ${num(waterWins * 100, 0)}% · dashed: equal binding · ODAC23 DFT`}
              />
              </>
            );
          })()
        ) : (
          <p className="muted">{pools.error ?? 'Loading pools…'}</p>
        )}
      </section>

      <section className="wrap section" id="metals">
        <h2 className="fig-title">Which metals make good CO2 capturers</h2>
        <p className="fig-sub">
          Every metal in the six pools, coloured by the share of MOFs containing it that are DFT hits. Each cell: MOFs with that metal ·
          hits. The agent sees the metals; it does not see this table.
        </p>
        {pools.data ? (
          <PeriodicTable rows={Object.values(pools.data.pools).flat()} label="Periodic table of DFT hit rates by metal" />
        ) : (
          <p className="muted">{pools.error ?? 'Loading pools…'}</p>
        )}
      </section>

      <section className="wrap section">
        <div className="split">
          <div>
            <h2 className="fig-title">Good materials found with 40 tests, by method</h2>
            <p className="fig-sub">Mean and 95% interval (hierarchical bootstrap). Dashed line: random expectation.</p>
            <ArmsChart
              arms={armsSorted.map((a) => ({ key: a.arm, label: ARM_LABEL[a.arm] ?? a.label, mean: a.hits_mean, lo: a.hits_ci[0], hi: a.hits_ci[1], strong: a.arm === 'A5' }))}
              reference={a0 ? { v: a0.hits_mean, label: 'random' } : undefined}
            />
          </div>
          <div>
            <h2 className="fig-title">Found so far, test by test</h2>
            <p className="fig-sub">Mean hits after each lookup. Bands are 95% intervals.</p>
            <DiscoveryChart series={curves} height={280} labelWidth={150} />
          </div>
        </div>
      </section>

      <section className="wrap section">
        <h2 className="fig-title">The statistical tests we committed to in advance</h2>
        <p className="fig-sub">
          Primary endpoint: A5 minus A3 in mean hits at 40, pool-stratified permutation test, one-sided. Frozen in{' '}
          <a className="link" href={PREREG_URL}>
            PREREG.md
          </a>{' '}
          at <span className="mono">{shortSha(R.prereg_sha)}</span>.
        </p>
        <div className="table-scroll" tabIndex={0}>
          <table className="data">
            <thead>
              <tr>
                <th>Comparison</th>
                <th className="r">Difference (hits)</th>
                <th className="r">95% CI</th>
                <th className="r">p</th>
              </tr>
            </thead>
            <tbody>
              {R.tests.map((t) => (
                <tr key={t.name}>
                  <td>{t.name}</td>
                  <td className="n r">{num(t.diff, 2, true)}</td>
                  <td className="n r">
                    {num(t.ci[0], 2)} to {num(t.ci[1], 2)}
                  </td>
                  <td className="n r">{t.p < 0.001 ? '< 0.001' : num(t.p, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="wrap section" id="reroll">
        <h2 className="fig-title">Try the simple methods yourself</h2>
        <p className="fig-sub">Re-roll the cheap baselines on any test pool with your own seed. Same definitions as the pre-registration.</p>
        {pools.data ? <Reroll pools={pools.data} results={R} /> : <p className="muted">{pools.error ?? 'Loading pools…'}</p>}
      </section>

      <section className="wrap section">
        <div className="split">
          <div>
            <h2 className="fig-title">How good is the quick physics estimate?</h2>
            <p className="fig-sub">MACE S against DFT S for every MOF in {pp}. Filled: hits. Never shown to the agent.</p>
            <div className="filters">
              <div className="seg seg-sm" role="group" aria-label="Pool">
                {R.pools.map((p) => (
                  <button key={p.pool} type="button" aria-pressed={p.pool === pp} onClick={() => setParityPool(p.pool)}>
                    {p.pool}
                  </button>
                ))}
              </div>
            </div>
            {parityRows.length > 0 && (
              <ScatterChart
                points={parityRows.filter((r) => r.S_mace != null).map((r) => ({ x: r.S_mace, y: S(r.E_CO2, r.E_H2O), strong: r.hit, id: r.id }))}
                xLabel="MACE S (eV)"
                yLabel="DFT S (eV)"
                ariaLabel={`MACE versus DFT score for ${parityRows.length} MOFs in pool ${pp}`}
                annotation={`n = ${parityRows.length} · ${parityRows.filter((r) => r.hit).length} hits · dashed: y = x`}
              />
            )}
          </div>
          <div>
            <h2 className="fig-title">What the physics models can and cannot tell</h2>
            <p className="fig-sub">Hit-ranking AUC (0.5 is chance) and energy error at the DFT geometry.</p>
            <dl className="kv-list">
              <div>
                <dt>MACE-MP-0, initial placements</dt>
                <dd>
                  {num(R.honesty.mace_auc, 2)} <span className="faint">[{ci(R.honesty.mace_auc_ci[0], R.honesty.mace_auc_ci[1], 2)}]</span>
                </dd>
              </div>
              <div>
                <dt>MACE after gas-only relaxation</dt>
                <dd>{num(R.honesty.gas_relax_auc, 2)}</dd>
              </div>
              <div>
                <dt>DFT at the initial geometry</dt>
                <dd>{num(R.honesty.dft_init_auc, 2)}</dd>
              </div>
              <div>
                <dt>MACE MAE at DFT geometry</dt>
                <dd>
                  {num(R.honesty.mace_mae_dft_geom, 3)} <span className="faint">eV</span>
                </dd>
              </div>
              <div>
                <dt>eqV2-ODAC MAE at DFT geometry</dt>
                <dd>
                  {num(R.honesty.eqv2_mae_dft_geom, 3)} <span className="faint">eV</span>
                </dd>
              </div>
            </dl>
            {R.honesty.notes && <p className="small" style={{ marginTop: 12 }}>{R.honesty.notes}</p>}
          </div>
        </div>
      </section>

      <section className="wrap section">
        <div className="split">
          <div>
            <h2 className="fig-title">Does the AI know what it knows?</h2>
            <p className="fig-sub">
              Proposer's stated P(hit) against the observed hit rate. Brier {num(R.calibration?.brier, 3)}, n = {R.calibration?.n ?? '—'}.
            </p>
            {R.calibration?.bins.length ? <ReliabilityChart bins={R.calibration.bins} /> : <p className="muted">No calibration data.</p>}
          </div>
          <div>
            <h2 className="fig-title">Seeing, cheating checks, and honest reports</h2>
            <p className="fig-sub">Checks that the model reads its plots, is not recalling answers, and cites what it says.</p>
            <dl className="kv-list">
              <div>
                <dt>Critic visual ρ vs computed ρ, MAE</dt>
                <dd>
                  {num(R.critic?.visual_rho_mae, 2)} <span className="faint">n = {R.critic?.n ?? '—'}</span>
                </dd>
              </div>
              <div>
                <dt>Leak probe L: rank refcodes, AUC</dt>
                <dd>{num(R.leak?.L_auc, 2)}</dd>
              </div>
              <div>
                <dt>Leak probe L2, AUC</dt>
                <dd>{num(R.leak?.L2_auc, 2)}</dd>
              </div>
              <div>
                <dt>Report claims verified</dt>
                <dd>
                  {R.claims ? `${R.claims.verified} / ${R.claims.n}` : '—'} <span className="faint">{R.claims ? `${R.claims.flagged} flagged` : ''}</span>
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section className="wrap section">
        <h2 className="fig-title">Best possible scores, per set</h2>
        <p className="fig-sub">What a perfect oracle, DFT at the unrelaxed geometry, and random would find with 40 lookups.</p>
        <div className="table-scroll" tabIndex={0}>
          <table className="data">
            <thead>
              <tr>
                <th>Pool</th>
                <th className="r">MOFs</th>
                <th className="r">Hits in pool</th>
                <th className="r">Perfect oracle</th>
                <th className="r">DFT, initial geometry</th>
                <th className="r">Random, expected</th>
              </tr>
            </thead>
            <tbody>
              {R.ceilings.map((c) => {
                const p = R.pools.find((x) => x.pool === c.pool);
                return (
                  <tr key={c.pool}>
                    <td className="n">{c.pool}</td>
                    <td className="n r">{p?.n ?? '—'}</td>
                    <td className="n r">{p?.hits ?? '—'}</td>
                    <td className="n r">{c.oracle_hits}</td>
                    <td className="n r">{c.dft_init_hits}</td>
                    <td className="n r">{num(c.random_expected, 2)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="wrap section">
        <h2 className="fig-title">Every material it tested</h2>
        <p className="fig-sub">From the ledger of each Glimmer agent run.</p>
        {index.data && pools.data ? <HitsAndMisses index={index.data} pools={pools.data} /> : <p className="muted">Loading…</p>}
      </section>
      <section className="wrap">
        <p className="small" style={{ marginTop: 48 }}>
          results.json generated {R.generated} · status {R.status} · {count(R.arms.reduce((s, a) => s + a.n_runs, 0))} campaigns in total.
        </p>
      </section>
    </Page>
  );
}
