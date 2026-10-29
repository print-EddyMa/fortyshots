# fortyshots pre-registration

**Status: DRAFT, not frozen.** It becomes binding when the commit that contains it is tagged `prereg-v1`, before any campaign on a test pool (P1–P5) is recorded. Edits after the tag go in `DEVIATIONS.md`, never here.

## 1. Question

Given 40 DFT lookups in a pool of 500 MOFs, does an LLM agent (Muse Glimmer 30B, run locally) that plans, uses cheap tools and learns from the labels it buys, find more humid-air CO2 binders than the strongest simple baseline with the same budget and the same information?

## 2. Hypotheses

- **H1 (primary).** Mean hits@40 of the full agent (A5) is greater than that of MACE-greedy (A2), the strongest baseline on the test pools.
- **H1b.** A5 is greater than RF-UCB (A3), the random-forest active learner.
- **H2.** A5 is greater than random (A0).
- **H3.** A5 is greater than the same agent without the surrogate tool (A6). This isolates what the LLM adds beyond the shared random forest.

All are one-sided. We expect H2 to hold. We have no confident expectation for H1, H1b or H3, and both a loss and a tie will be reported.

**Why A2 is the primary comparator (disclosed).** Before the freeze, with every baseline computed on P1–P5, MACE-greedy (A2) was the strongest baseline: 2.20 hits@40 against 1.39 for RF-UCB and 1.13 for random. MACE's ranking over the whole pool is no better than chance (hit AUC 0.49), but its 40 most strongly binding picks are enriched about two-fold. We compare against the strongest baseline we know of, not the one easiest to beat. The designers knew these baseline numbers before the freeze; they never saw any A5 result on a test pool.

## 3. Universe, labels, hit definition

These are fixed in `CONTRACTS.md`, and this file repeats them.

- **Universe.** ODAC23 IS2R pristine MOFs with both CO2 and H2O single-adsorbate DFT labels and at most 200 atoms including the adsorbate. Train split: 3,026 MOFs, 85 hits.
- **Labels.** For each gas, the minimum `y_relaxed` over the ODAC placements, in eV (PBE+D3). These are reference labels, not ground truth. The empty-MOF references were later revised in ODAC25.
- **Config filter (disclosed).** `physics/extract.py` drops every ODAC config with |`y_relaxed`| ≥ 5 eV before anything else is built. This is a filter on the DFT label, and it decides which placements exist downstream: the placements the label is the minimum over, the placements MACE is run on (up to 5 per gas, by config name, from those kept), and, if every placement of a gas is dropped, whether the MOF is in the universe at all. It is applied once, before the pools are drawn, so it affects all arms equally; but the cheap oracle never sees placements that the filter removed for having extreme DFT energies.
- **Hit.** `E_CO2 < −0.5 eV` and `E_CO2 < E_H2O`.
- **Score** (lower is better). `S = E_CO2 + max(0, E_CO2 − E_H2O + 0.1)`.
- **Pools.** Six disjoint pools of 500 train-split MOFs (seed 20261029):
  - **P0** is for development and prompt tuning only.
  - **P1–P5** are test pools, with 14, 12, 16, 15 and 13 hits, 70 in total. The base rate is 0.028.
  - **V** (the val split, 84 MOFs, 3 hits) is descriptive only.

## 4. Budget and protocol

40 lookups per campaign, in 4 rounds of 10. A lookup returns `E_CO2`, `E_H2O` and `hit` for that MOF only. `request_dft` enforces the budget in code. A campaign that aborts (the 45-minute cap, or three consecutive failed attempts at one model call — timeout, unreachable server or HTTP error — with backoff and a server health check between attempts) is never dropped or re-rolled; this is intention to treat. **Autofill:** lookups still unspent when a campaign ends are bought by the harness: remaining lookups are bought in S_mace order (arm-neutral). Autofilled picks count as the agent's picks, and the ledger records them. Every pick chosen by code rather than the model (Proposer fallbacks, live-mode reject replacements, end-of-run autofill) is listed in `summary.harness_picks` / `summary.harness_pick_ids`; `summary.autofill` counts the end-of-run fill only. In A4 the Proposer's rationales are capped at 140 characters. A model call that comes back empty with finish_reason=length, or is refused for context size, is retried once with older tool results trimmed and images dropped; a length cut also lowers reasoning_strength one level for that retry.

## 5. Arms

All arms see the same information: label-free descriptors (`data/descriptors.parquet`), the cheap MACE values (`data/mace_cache.parquet`), and the labels they have bought themselves.

| Arm | Definition | Runs |
|---|---|---|
| A0 Random | Uniform without replacement | 50 seeds × P1–P5 |
| A1 Heuristic | Groups in this order: open metal site with 3.3 ≤ PLD ≤ 6 Å; then open metal site at any PLD; then the rest. Within a group: cheaper `metal_cost_tier` first, then higher metal-site density (metal atoms per nm³), then a seeded random tie-break. | 50 × P1–P5 |
| A2 MACE greedy | The 40 lowest `S_mace`, seeded tie-break | 50 × P1–P5 |
| A3 RF-UCB | Round 1: 10 uniform random. Rounds 2–4: random forest (below) on all bought labels; buy the 10 unbought MOFs with the lowest `mean − 1.0·std` of predicted S. | 50 × P1–P5 |
| A4 Glimmer zero-shot | Explores with the tools, then orders 40 at once with no label feedback | k × P1–P5 |
| **A5 Glimmer agent** | Full loop: Director, Proposer (P(hit) per pick), Critic (vision), 4 rounds. `calibrate_oracle` is **mandatory from round 2**. | k × P1–P5 |
| A6 Agent, no surrogate | A5 without `fit_surrogate` | k × P1–P5 (see §7) |
| L Leak probe | Glimmer ranks the raw CSD refcodes of P1 with nothing else; we report the AUC of that ranking against hits | 1 |

**Random forest (A3 and the agent's `fit_surrogate`, shared code in `bench/surrogate.py`).**
- Model: scikit-learn `RandomForestRegressor` with `n_estimators=500` and all other settings at their defaults. `random_state` is the seed for A3 and a fixed value for the tool.
- Target: the DFT score S.
- Features: natoms, volume, density, LCD, PLD, ASA, void fraction, metal atom count, metal cost tier, open-metal-site flag, metal-site density, `E_CO2_mace`, `E_H2O_mace`, `S_mace`, plus presence flags for the 12 most common metals in P0 (Zn, Cu, Cd, Mn, Co, Ni, Fe, La, Al, Nd, Eu, Gd).
- Uncertainty: std is the spread of the per-tree predictions.
- These hyperparameters are not tuned. Any tuning would happen only on P0, and would be recorded here before the freeze.

**Baseline seeds.** A0 and A3 differ by seed. A1 and A2 are deterministic up to ties, so their 50 seeds are near-identical by construction. The RNG for arm *a*, pool *p* and seed *s* is `numpy.random.default_rng([a, p, s, 20261029])`.

## 6. Endpoints

**Primary.** Mean hits@40, A5 minus A2. One-sided pool-stratified permutation test:
- 10,000 permutations, shuffling arm labels within each pool.
- Statistic: the mean over pools of (A5 pool mean − A2 pool mean).
- α = 0.05.

**Secondary** (reported with CIs; no multiplicity correction; labelled exploratory):
1. A5 − A3, A5 − A0 and A5 − A6 hits@40, using the same test.
2. Enrichment: for each pool, the arm's mean hits@40 in that pool ÷ (40 × that pool's base rate, hits ÷ 500); then the mean of these ratios over pools, each pool weighted equally. (This is not the pooled mean hits@40 divided by the pooled random expectation.)
3. Lookups to first hit (41 when a run has no hit), and lookups per hit (total lookups ÷ total hits).
4. Discovery curve: mean cumulative hits after k = 0…40 lookups, with pointwise 95% bands.
5. Calibration of the agent's P(hit) over every A5 pick that has a probability: Brier score and a reliability diagram (10 equal-width bins).
6. Critic `visual_rho` against the computed Spearman ρ of the same round's calibration: mean absolute error, over critiques made with the parity image (`critique.vision` true) only. Text-only critiques are counted and reported separately (`n_text`), never scored.
7. Claim-check pass rate (claims verified ÷ claims), and the count of schema failures.
8. Leak probe AUC.
9. Harness-pick share, per LLM arm: the share of a campaign's purchases chosen by code rather than the model (proposer fallbacks, live reject replacements and end-of-run autofill; `summary.harness_picks`), averaged like hits (per pool, then over pools). Descriptive, not tested. Runs recorded before the field existed count as 0.

**Ceilings, per pool at 40 lookups.**
- Oracle: min(40, hits in the pool).
- Greedy by DFT at the initial, unrelaxed geometry (`E_*_init`).
- Random expectation: 40 · h_k / 500.

**Physics honesty panel** (descriptive; never shown to the agent):
- MACE hit AUC on P1–P5, with a bootstrap CI.
- Gas-relaxation AUC.
- DFT-at-initial-geometry AUC.
- MACE and eqV2-ODAC MAE at DFT geometries.

## 7. Seeds per pool for LLM arms (k)

Throughput is measured by `./lab doctor` and the P0 pilot, and k is fixed at the freeze. With five test pools we prefer more pools to more seeds. Every arm runs on all five pools at seed 0 before any pool gets seed 1.

| Median pilot campaign time | A5 | A4 | A6 |
|---|---|---|---|
| ≤ 15 min | k = 3 (15 runs) | k = 1 | k = 2 |
| ≤ 25 min | k = 2 (10 runs) | k = 1 | k = 1 |
| > 25 min | k = 1 (5 runs) | k = 1 | k = 1, on P1–P3 only |

Recording order per night: A5 first, then A4, A6, L. A run that crashes is resumed from its last checkpoint. A model call that failed before the crash is sent again; its logged failures do not end the run. A run that cannot finish within the 45-minute cap is logged as aborted and counts (§4). Runs are never re-rolled.

## 8. Statistics

- **Point estimates.** An arm's mean is the mean over pools of the per-pool mean over runs, so every pool has equal weight.
- **Uncertainty.** Hierarchical bootstrap, 10,000 draws: resample pools with replacement, then runs within each pool with replacement; report the 95% percentile interval.
  - When an arm covers fewer than 5 pools, pools are held fixed and only runs are resampled. That CI is conditional on those pools, and we say so.
  - Differences resample pools jointly, and runs independently per arm.
- **Fixed seeds.** Every Monte-Carlo routine has a fixed seed derived from the name of the quantity (CRC32), so `python -m bench.verify` reproduces every number exactly.
- **Computation.** `bench/stats.py` and `bench/results.py` compute everything, from `runs/`, `data/` and `results/baselines.parquet`, with no model calls.
- **Integrity.** Each run's purchase order is rebuilt from its ledger (`dft_order` / `dft_result` events); a summary that disagrees with its ledger, header, pool or labels is a verification failure and the run is left out. Runs whose budget is not 40 are left out with a logged reason. No result is computed for a pool whose cheap MACE values are incomplete; missing values are never imputed or ranked last.
- **Fingerprint.** `results.json` reports `prereg_sha` as the commit of tag `prereg-v1`. `python -m bench.verify` checks that every analysed non-mock run was recorded from that commit or a descendant of it.

## 9. Reporting rules (pre-committed)

- **If H1 holds** (p < 0.05): the headline is A5 hits@40 with its CI and enrichment. It always names both baselines, for example "N hits in 40 (95% CI …) vs X for random and Y for RF-UCB".
- **If H1 does not hold:** the headline is A5 against random and RF-UCB, stating the loss or tie to RF-UCB on the results page. The story becomes the simulator check (calibration, Critic) rather than the win.
- **Showcase run.** The run featured in the video and on the landing page is the A5 run on P1 with the median hits@40. Ties go to the fewer lookups to first hit, then the lowest seed. It is chosen by rule, not by hand.
- **Arms and CIs.** Every arm, every test and every ceiling is reported, including unfavourable ones.

## 10. Leakage controls

- **Hashed IDs.** IDs are salted SHA-256 hashes of the CSD refcode, and CIF titles are stripped. No refcode appears in any prompt, cassette, run file or web bundle; only `data/id_map.parquet` holds refcodes.
- **What the agent sees.** Its tools expose descriptors, cheap MACE values, renders and the labels it bought. `E_*_init` and unbought labels never reach it.
- **Leak probe.** L tests whether Glimmer recalls ODAC answers from refcodes.
- **Tuning.** All prompt and tool tuning happens on P0 only.
- **Disclosure.** Before the freeze, the designers knew the aggregate MACE hit AUC from the research phase (0.49 [0.40–0.58] on 200 training MOFs, `research/out/fast_strat_mace.jsonl`). They also measured MACE's single-point behaviour on the P1 MOFs while building the cache. That knowledge shaped one design decision: `calibrate_oracle` is mandatory from round 2. The agent itself is never told the AUC, and `cheap_score` returns raw values with no error card.

## 11. What is not evidence

- **Prospective mutants** (Chemist mode) are ranked by the campaign surrogate. MACE and eqV2 binding energies for them appear greyed out and labelled "unvalidated · ML only". They never enter any benchmark number.
- **eqV2-ODAC** was trained on ODAC23, so it is used only as a cross-check and in the honesty panel, never as an arm.

## 12. Deviations

Any change after `prereg-v1` gets an entry in `DEVIATIONS.md` that gives:
- the date;
- what changed;
- why;
- whether it was made before or after seeing any test-pool result;
- its expected effect on the endpoints.

A deviation never silently replaces a pre-registered analysis: both are reported.
