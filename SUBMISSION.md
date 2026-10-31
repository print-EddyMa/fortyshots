# Cerebral Valley submission

Copy each field into the form. Fill every `{{...}}` from `results/results.json` and the README first; never type a number that is not in the results. Submit by **Wed Nov 4, 12:00 ET** (deadline 21:00 ET).

**Project name**
fortyshots

**Tagline** (one line)
An AI scientist that decides which experiments to run. Find materials faster, with Meta's open Muse Glimmer running on a laptop.

**Track**
Indie

**Theme**
Push the Frontier

**Team**
fortyshots (solo: Eddy Ma)

**Summary** (183 words)

fortyshots is an AI scientist for materials discovery. You describe the material you need; it decides which candidates are worth an expensive simulation, learns from each result, and shows its reasoning. It runs Meta's open-weights Muse Glimmer 30B entirely on a MacBook, with no API key or cloud. One model plays four roles: planner, picker, critic (using vision on its own charts and 3D structures) and reporter, and every number it writes is checked against the run log.

Our first target is CO2 capture from humid air: among 500 metal-organic frameworks, find those that hold CO2 more strongly than water, with 40 accurate simulations from Meta FAIR's Open DAC dataset. We pre-registered the test and scored it blind on five independent sets. The agent found 1.4 of the hidden good materials per search, level with a random-forest baseline (1.4) but below the strongest simple method (2.2). Its confidence is well calibrated (Brier 0.087), and more than nine in ten report claims verify. It also proposes new candidate materials, built and relaxed by simulation for a person to approve. Every number reproduces with one command.

**Public repository**
https://github.com/print-EddyMa/fortyshots

**Live site**
https://print-EddyMa.github.io/fortyshots/

**Demo video** (3:00 or less; unlisted on an existing account; check that the link plays while logged out)
{{VIDEO_URL}}

**Meta models and tools used**
- Muse Glimmer 30B (open weights, Apache-2.0), run on our own hardware: MacBook Pro M4 24 GB, llama.cpp `llama-server`, a 3-bit GGUF (unsloth UD-Q3_K_XL) with Meta's `mmproj` vision projector and Meta's DFlash drafter for speculative decoding (3.0 → 6.8 tok/s measured on this Mac). Every role in the agent is Glimmer: native tool calls, json_schema output, vision on its own plots, cited reports.
- Meta FAIR ODAC23 dataset (CC-BY-4.0): the hidden DFT labels and the DFT-relaxed CO2 poses shown in the viewer.
- Meta FAIR EquiformerV2 ODAC checkpoint: cross-check for agent-proposed variants (not a benchmark arm, because it was trained on ODAC23).
- No Meta Model API and no partner inference were used, by choice: the project needs no signup or key of any kind.

**What was built during the hackathon**
Everything in the repository was written between Oct 29 and Nov 4, 2026; the first commit is tagged `hackathon-start` and the history is not squashed. That includes the data pipeline and pools, the MACE cache, the agent and its tools, the claim checker, the baselines and statistics, the website, the Mac runtime, and every recorded campaign. Reused components (Glimmer weights, llama.cpp, ODAC23, MACE-MP-0, EquiformerV2, Zeo++, 3Dmol.js, fonts) are listed in PRIOR_WORK.md, and the README has a table of components with commit ranges.

**Bonus categories**
- Best Build on Meta Open Weight Models: the whole system is Muse Glimmer 30B on our own laptop; every run header records the GGUF sha256, llama.cpp build, server flags, machine and network state.
- Best Agentic Use-Case: multi-round autonomous campaigns, a DFT budget enforced in code, recovery from tool errors, resume after a crash, a human approval gate in live mode, and lookups per hit as the metric.
- Community Vote: the public runs gallery at https://print-EddyMa.github.io/fortyshots/#/runs
- Most Viral on X: thread at {{X_THREAD_URL}}

**Cover image**
`web/public/og.png` (1200×630; the website build can generate one, see `web/package.json` script `og`)

**Anything else for judges** (optional field, if the form has one)
Headline numbers come with 95% intervals and name both baselines. The results page also shows where the agent lost or tied, the leak probe, and the physics-honesty panel. Limitations: the labels are existing DFT reference values, the candidates are known MOFs (picks, not discoveries), and agent-proposed variants are unvalidated.

---

Before pressing submit:
- [ ] Every `{{...}}` on this page is filled and matches `results/results.json`.
- [ ] Repository is public; tags `hackathon-start` and `prereg-v1` are pushed.
- [ ] Site loads at 390 px and 1440 px wide.
- [ ] Video link plays in a private browser window.
- [ ] Track is set to Indie.
