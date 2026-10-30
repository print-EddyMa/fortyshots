import { Page, Prelim } from '../components/Chrome';
import { PREREG_URL, REPO_URL } from '../lib/config';
import { hasMockRuns, isPreliminary } from '../lib/data';
import { num, shortSha } from '../lib/format';
import { useIndex, useResults } from '../lib/useData';

const repoPath = REPO_URL.replace(/^https?:\/\//, '');

export function MethodPage() {
  const results = useResults();
  const index = useIndex();
  const R = results.data;
  const h = R?.honesty;
  const sections = [
    ['task', 'The task'],
    ['agent', 'The agent'],
    ['arms', 'Arms'],
    ['prereg', 'Pre-registration'],
    ['labels', 'Label caveats'],
    ['leakage', 'Leakage controls'],
    ['offline', 'Offline'],
    ['reproduce', 'Reproduce'],
    ['changed', 'What changed'],
    ['credits', 'Credits'],
  ];
  return (
    <Page current="/method">
      <section className="prose-wrap page-head">
        <h1 className="h-section">
          <span>How it works.</span>
          <span className="second">What the AI does, and how we test it.</span>
        </h1>
        <nav className="toc" aria-label="On this page">
          {sections.map(([id, t]) => (
            <a key={id} href={`#/method`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' }); }}>
              {t}
            </a>
          ))}
        </nav>
        {isPreliminary(R, index.data) && <Prelim status={R?.status} mockRuns={hasMockRuns(index.data)} />}
      </section>
      <article className="prose-wrap prose">
        <p>
          <strong>In plain terms.</strong> Machines that pull CO2 out of the air (direct air capture) need a sponge-like material
          that grabs CO2 and holds it. Metal–organic frameworks (MOFs) are promising sponges: crystals full of pores a few atoms
          wide. The catch is humidity: in most of them, water takes the spot CO2 needs. Finding the few that work means running a
          slow, accurate quantum-chemistry simulation (DFT) on each candidate, and a lab can only afford a few dozen.
        </p>
        <p>
          fortyshots is an AI research agent that decides which candidates are worth those expensive simulations. It runs on
          Meta&apos;s open Muse Glimmer 30B model, entirely on a laptop. In each of four rounds it explores the candidates with
          tools, picks ten to test with a stated probability for each, critiques its picks by looking at structures and charts,
          and learns from the results. A person approves each batch. Below is exactly how we built and tested it.
        </p>

        <h2 id="task">The task</h2>
        <p>
          The universe is every pristine MOF in Meta FAIR's ODAC23 IS2R set that has both a CO2 and an H2O DFT label and at most
          200 atoms with the adsorbate: 3,026 MOFs in the train split, 85 of them hits. Six disjoint pools of 500 MOFs are drawn
          with a fixed seed. P0 is for development only; P1 to P5 are test pools.
        </p>
        <ul>
          <li>
            The DFT label is the minimum relaxed adsorption energy over the ODAC placements, per gas, in
            eV (PBE+D3): <code>E_CO2</code> and <code>E_H2O</code>.
          </li>
          <li>
            A hit has <code>E_CO2 &lt; −0.5</code> and <code>E_CO2 &lt; E_H2O</code>.
          </li>
          <li>
            The DAC score, lower is better: <code>S = E_CO2 + max(0, E_CO2 − E_H2O + 0.1)</code>.
          </li>
          <li>
            The budget is 40 lookups, bought in 4 rounds of 10. The base rate is {R ? `${num(R.base_rate * 100, 1)}%` : '—'}, so random
            search expects {R ? num(R.base_rate * 40, 1) : '—'} hits.
          </li>
        </ul>

        <h2 id="agent">The agent</h2>
        <p>
          One model plays four roles: Muse Glimmer 30B, Meta's open-weights model, as a 3-bit GGUF with Meta's DFlash drafter under llama.cpp on a 24 GB
          MacBook.
        </p>
        <ol>
          <li>
            The <span className="mono">Director</span> explores the pool with parallel tool calls: filters, descriptors, cheap
            MACE-MP-0 energies, renders, and from round 2 on a calibration of the cheap oracle against the labels it has bought.
          </li>
          <li>
            The <span className="mono">Proposer</span> commits to ten picks as JSON, each with a rationale, the tool calls it
            relies on, and a probability of being a hit. Those probabilities are scored (Brier, reliability).
          </li>
          <li>
            The <span className="mono">Critic</span> reads its own parity plot and a structure render through vision, states the
            rank correlation it sees, and may veto up to three picks.
          </li>
          <li>
            The <span className="mono">Reporter</span> writes the campaign report, citing a tool call for every number. A
            deterministic claim checker verifies each number and ID against the cited output.
          </li>
        </ol>
        <p>
          The budget lives in code: <code>request_dft</code> refuses the 41st lookup. The agent is not told how good the cheap
          simulator is. It has to find out from the labels it pays for.
        </p>

        <h2 id="arms">Arms</h2>
        <div className="table-scroll" tabIndex={0}>
          <table className="data">
            <thead>
              <tr>
                <th>Arm</th>
                <th>Definition</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['A0', 'Random: 40 uniform picks.'],
                ['A1', 'Heuristic: open metal site and PLD 3.3–6 Å, ranked by metal cost.'],
                ['A2', 'MACE-greedy: the 40 lowest MACE scores.'],
                ['A3', 'RF-UCB: round 1 random, then a 500-tree random forest on descriptors and MACE, acquiring by mean − std.'],
                ['A4', 'Glimmer zero-shot: explores, then orders all 40 with no feedback.'],
                ['A5', 'Glimmer agent: the full loop.'],
                ['A6', 'Glimmer agent without the surrogate tool, to isolate the model’s own contribution.'],
                ['L', 'Leak probe: Glimmer ranks raw CSD refcodes with nothing else; AUC against hits.'],
              ].map(([a, d]) => (
                <tr key={a}>
                  <td className="n">{a}</td>
                  <td style={{ whiteSpace: 'normal' }}>{d}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h2 id="prereg">Pre-registration</h2>
        <p>
          Arms, endpoints, the number of seeds and the reporting rule were frozen in{' '}
          <a className="link" href={PREREG_URL}>
            PREREG.md
          </a>{' '}
          (tag <span className="mono">prereg-v1</span>, {R ? <span className="mono">{shortSha(R.prereg_sha)}</span> : 'hash on the results page'})
          before any test-pool run. Changes after the freeze are listed in DEVIATIONS.md.
        </p>
        <ul>
          <li>Primary endpoint: mean hits at 40 for A5 minus A3, pool-stratified permutation test, one-sided.</li>
          <li>Secondary: A5 vs A0 and A6, enrichment, lookups to first hit, the discovery curve, Brier score, Critic accuracy, claim-check pass rate.</li>
          <li>Uncertainty: hierarchical bootstrap over pools, then seeds; 95% intervals. With few LLM runs per arm the intervals are wide, and the results page says so.</li>
          <li>If A5 does not beat A3, the headline stays “hits in 40 against random” and the loss or tie is stated on the results page.</li>
        </ul>

        <h2 id="labels">Label caveats</h2>
        <ul>
          <li>ODAC23 labels are DFT reference values, not experiments. PBE+D3 has known errors for physisorption.</li>
          <li>The label is the minimum over the placements ODAC tried. A better site may exist that nobody computed.</li>
          <li>Some DFT relaxations stop in shallow minima, so a few true binders read as misses.</li>
          <li>
            The cheap oracle is weak here: MACE-MP-0 ranks hits at AUC {h ? num(h.mace_auc, 2) : '—'} at the initial placements,
            against {h ? num(h.dft_init_auc, 2) : '—'} for DFT at the same geometry. That gap is the point of the experiment, not a
            bug in it.
          </li>
        </ul>

        <h2 id="leakage">Leakage controls</h2>
        <ul>
          <li>IDs are salted hashes of CSD refcodes; CIF titles are stripped. No refcode reaches a prompt, ledger or this site.</li>
          <li>Only <code>request_dft</code> reads the label file, and only for the rows bought. The model has no file or shell tools.</li>
          <li>The leak probe asks Glimmer to rank raw refcodes. An AUC near 0.5 means it is not recalling answers.</li>
          <li>All prompt tuning used pool P0.</li>
        </ul>

        <h2 id="offline">Offline</h2>
        <p>
          Every run starts with an attestation header: GGUF file and sha256, quantization, llama.cpp build and flags, machine,
          OS, network state, and the pre-registration hash. The client refuses any non-loopback model URL. The console shows the
          header in its Model tab.
        </p>

        <h2 id="visuals">Where the pictures come from</h2>
        <ul>
          <li>
            <span className="muted">Landing structure.</span> The test-pool hit with the largest cavity whose CO2 moves at least 0.5 Å in the
            DFT relaxation, chosen by rule in <code>bench/chem.py</code>. Atoms move from the ODAC initial placement to the DFT-relaxed
            geometry in its structure file. The mesh is the surface where a 1.65 Å probe (half CO2's kinetic diameter) touches the
            framework's van der Waals surface, from a grid computed on the relaxed framework.
          </li>
          <li>
            <span className="muted">Atlas.</span> A t-SNE (scikit-learn, fixed seed) of the 3,000 pool MOFs from the descriptors and MACE
            values the agent can see; DFT labels are not inputs (<code>bench/atlas.py</code>). Baseline paths are seed 0 of each arm in{' '}
            <code>results/baselines.parquet</code>; the agent path is the purchase order in the run ledger.
          </li>
          <li>
            <span className="muted">Binding site.</span> Distances from each CO2 atom to its nearest framework atom and to the nearest metal,
            minimum image across the periodic cell, at the DFT-relaxed geometry.
          </li>
          <li>
            <span className="muted">Charges.</span> ODAC23 DDEC6 net atomic charges (Meta FAIR, CC-BY-4.0), matched to each structure's atoms
            by element and position (<code>bench/chem.py ddec</code>).
          </li>
        </ul>

        <h2 id="reproduce">Reproduce</h2>
        <pre tabIndex={0}>
          <code>{`git clone ${repoPath}
cd fortyshots
./lab setup
fortyshots verify   # recompute results.json from runs/ and data/, no model
./lab live          # run a campaign on your own machine`}</code>
        </pre>
        <p className="muted">
          Every model call is stored in a cassette, so a run replays byte for byte without the model. The website reads the same
          ledgers.
        </p>

        <h2 id="changed">What changed since the application</h2>
        <p>
          The application pitched Llama with UMA. UMA, ODAC25 and the Muse API need gated access or signups, and this project
          uses no signups. So fortyshots runs on Meta's open-weights Muse Glimmer 30B and Meta FAIR's openly downloadable ODAC23
          data and eqV2 model. Measuring the cheap potentials also showed that they miss chemisorption, so the agent treats
          physics as noisy evidence and spends a DFT budget to decide.
        </p>

        <h2 id="credits">Credits</h2>
        <ul>
          <li>ODAC23 and eqV2-ODAC by Meta FAIR (CC-BY-4.0).</li>
          <li>Muse Glimmer 30B by Meta (Apache-2.0), served with llama.cpp (MIT).</li>
          <li>MACE-MP-0 (MIT). 3Dmol.js (BSD-3-Clause). Geist and Geist Mono (SIL OFL 1.1).</li>
        </ul>
        <p className="small">
          Agent prose on this site is written by Muse Glimmer 30B. fortyshots is not affiliated with or endorsed by Meta.
        </p>
      </article>
    </Page>
  );
}
