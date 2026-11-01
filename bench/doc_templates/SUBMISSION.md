# Cerebral Valley submission

Copy each field into the form. Every number comes from `results/results.json`, and `./lab verify` recomputes it. Submit by **Wed Nov 4, 12:00 ET** (the hard deadline is 21:00 ET).

**Project name**
fortyshots

**Tagline**
fortyshots turns a materials goal into a tested shortlist. Meta's Muse Glimmer decides which experiments to run; physics decides what's true; every number it writes is checked.

**Track**
Indie

**Theme**
Push the Frontier

**Team**
fortyshots (solo: Eddy Ma)

**Public repository**
https://github.com/print-EddyMa/fortyshots

**Live site** (no login; replay every run)
https://print-EddyMa.github.io/fortyshots/

**Demo video** (3:00; unlisted; check the link plays while logged out)
{{VIDEO_URL}}

---

## Inspiration

Machines that pull CO2 out of the air need a sponge: a material that grabs CO2 and lets it go again. Metal–organic frameworks are the best candidates, crystals full of pores a few atoms wide. In 85% of the 3,000 we screened, water grabs the binding site first. Checking a single candidate accurately takes a quantum-chemistry simulation (DFT) and hours of supercomputer time. So a lab tests a few dozen out of thousands, and the choice of which few is mostly judgment.

We wanted to know whether an open AI model could make that choice like a scientist: plan, commit to predictions, look at the evidence, learn, and show its work. And whether we could check that honestly.

## What it does

You give it a goal: *find a material that holds CO2 more strongly than water.* fortyshots then runs a campaign:

**500 candidates → plan → pick 10 with a stated confidence → critique the picks by looking at structures and charts → you approve → 10 simulations come back → learn → repeat ×4 → cited report → proposed new materials**

- **One model, four roles.** Muse Glimmer 30B plans the search with tools, commits to picks with a probability for each, critiques its own picks with vision, and writes the report.
- **The AI decides what to test; physics decides what's true.** Results come only from the simulations: Meta FAIR's Open DAC 2023 DFT, revealed one paid lookup at a time. A deterministic checker matches every number in the report against the computation it cites.
- **You stay in charge.** You approve each batch. The materials it proposes (new linkers, swapped metals) are built and relaxed by simulation, labelled "unvalidated", and wait for your approval.
- **Everything is replayable.** Every campaign is an append-only log you can watch step by step on the site.

## How we built it

- **Agent:** Python, with the llama.cpp `llama-server` OpenAI-compatible API and native tool calls. Each role gets a JSON schema. The budget is enforced in code, and a crashed campaign resumes from its log.
- **Physics:**
  - Cheap estimates for every candidate: MACE-MP-0 single points (30,000+ calculations, precomputed).
  - The 40 "experiments": ODAC23 DFT labels.
  - Proposed materials: MACE relaxation plus validity checks.
  - Pore geometry: Zeo++.
- **Benchmark:** six disjoint pools of 500 MOFs. Four baselines (random, a heuristic, greedy on the cheap estimate, and a random-forest active learner) ran 250 times each. Statistics use a pool-stratified permutation test and a hierarchical bootstrap. Everything was pre-registered and tagged `prereg-v1` before the first test run.
- **Site:** React, 3Dmol.js and hand-drawn SVG and canvas charts, served as a static GitHub Pages site. It includes:
  - the live console;
  - a chemical-space map of all 3,000 MOFs;
  - DFT binding geometries with Å distances;
  - a periodic table of hit rates.

## How we used Meta's AI tooling

- **Muse Glimmer 30B** (open weights) runs every role, on our own laptop, with no API key or cloud. Its abilities, each measured by our `./lab doctor`:
  - parallel tool calls;
  - schema-constrained JSON;
  - vision: it reads its own parity plot to within 0.10 of the true correlation;
  - controllable reasoning strength.
- **Meta's DFlash drafter** (speculative decoding): **3.0 → 6.8 tokens/s** on a base M4 MacBook (`./lab bench`, 68% of drafted tokens accepted). That made a full campaign fit in under an hour.
- **Meta's vision encoder (mmproj):** the Critic looks at 3D renders and its own calibration charts before vetoing picks.
- **Meta FAIR's Open DAC 2023:** the ground truth (CC-BY-4.0), and the DFT-relaxed CO2 positions shown on the site.
- **Meta FAIR's EquiformerV2 (ODAC):** a cross-check for proposed materials.

## Challenges we ran into

- **110-minute campaigns.** On a base M4 the first estimate was 110 minutes per campaign. Prompt reading turned out to be a third of it. Glimmer's chat template writes the tool list and the reasoning strength at the very top of the prompt, so any role with a different setting threw away the cache. One shared tool list, one strength and Meta's DFlash drafter brought it to a **median of 52 minutes**.
- **A model hiccup could brick a run.** One timeout on the laptop made a campaign impossible to resume, and it silently dropped out of the results. Failures are now retried with health checks. A crashed run resumes cleanly, and a run that can't finish is counted as aborted, never dropped.
- **The agent sorted by the simulator every round.** In early pilots it never searched near a hit. We added a `similar_to` tool and a short strategy prompt. In a test run it found a hit and immediately searched its neighbours. The trace shows it.
- **Our cheap simulator was both useless and useful.** Over the whole pool it ranks candidates at chance (AUC 0.49), yet its top 40 hold twice the random rate of hits. That makes "greedy on the simulator" the hardest baseline. We made it the primary comparison, and disclosed that we knew this before freezing.

## Accomplishments that we're proud of

- **12 benchmark campaigns on a laptop** (the full agent, the no-feedback version and both leak probes on the test sets), plus 6 development pilots, all replayable on the site.
- **Pre-registered and scored blind.** 0 mismatches when `./lab verify` recomputes every published number from the logs in about 15 seconds, with no model.
- **Its confidence is honest.** The Brier score is 0.087 over 200 stated probabilities.
- **It checks its own claims.** 224 of 241 numbers in its reports verified; the 17 that didn't are flagged on screen.
- **183 automated tests.** An independent audit found no path for a hidden label to reach the model.

## Results, honestly

With 40 simulations among 500 candidates (about 14 good ones hidden in each), averaged over five independent sets:

| | Good materials found |
|---|---|
| Random picks | 1.1 |
| Random-forest active learner | 1.4 |
| **fortyshots** | **1.4** |
| Greedy on the cheap simulator (best simple method) | 2.2 |

fortyshots matches the machine-learning baseline and does not yet beat the best simple method. We pre-registered that comparison, and we report it as it came out. With five runs per arm the intervals are wide (fortyshots: 1.0–1.8).

## What we learned

An open 30B model can run a full, self-checking research loop on a laptop. It plans, uses tools, reads its own plots, states calibrated confidence and cites its evidence. What it can't do yet is out-rank a strong simple heuristic with 40 shots. So its value today is in honesty, transparency and setup speed, not in raw hit rate. The design rule held up: let the model choose, let physics decide, and check every claim.

## What's next

- **Start from the best simple method.** Let the agent re-rank and filter the greedy shortlist with its reasoning and vision. That's the next pre-registered test.
- **Real queues.** Replace the stored DFT labels with live DFT jobs, then with a lab's own synthesis and adsorption measurements.
- **More materials.** The loop is the same for battery electrolytes and catalysts. Only the tools and the scoring change.

Same rule: the AI decides what to test, physics decides what's true.

---

**Bonus categories**
- **Best Build on Meta Open Weight Models:** the whole system is Muse Glimmer 30B on our own laptop. Every run header records the GGUF sha256, llama.cpp build, server flags, machine and network state.
- **Best Agentic Use-Case:** multi-round autonomous campaigns with tools, a budget enforced in code, crash recovery, a human approval gate, and a self-check against the data it paid for.
- **Community Vote:** the runs gallery at https://print-EddyMa.github.io/fortyshots/#/runs
- **Most Viral on X:** thread at {{X_THREAD_URL}}

**Cover image**
`web/public/og.png` (1200×630)

**Built during the hackathon**
Everything in the repository was written between Oct 29 and Nov 4, 2026. The first commit is tagged `hackathon-start`, and the history is not squashed. Reused components (Glimmer weights, llama.cpp, ODAC23, MACE-MP-0, EquiformerV2, Zeo++, 3Dmol.js, fonts) are listed in PRIOR_WORK.md.

**Limitations** (also on the site)
- The labels are existing DFT reference values, so these are picks among known materials, not new discoveries.
- The proposed materials are machine-learning only and unvalidated.
- The leak probe (0.65) sits at the edge of chance, so slight recall of known materials can't be ruled out.
- One secondary arm (A6) was not recorded in time; see DEVIATIONS.md.

---

Before pressing submit:
- [ ] `{{VIDEO_URL}}` and `{{X_THREAD_URL}}` are filled.
- [ ] The repository is public, and the tags `hackathon-start` and `prereg-v1` are on GitHub.
- [ ] The video link plays in a private browser window.
- [ ] Your track on Cerebral Valley is set to Indie.
