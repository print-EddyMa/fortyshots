# Cerebral Valley submission

Copy each field into the form. Fill every `{{...}}` from `results/results.json` and the README first; never type a number that is not in the results. Submit by **Wed Nov 4, 12:00 ET** (deadline 21:00 ET).

**Project name**
fortyshots

**Tagline** (one line)
An offline AI lab that decides which expensive experiment to run next: Meta's open Muse Glimmer 30B on a laptop, scored blind against DFT.

**Track**
Indie

**Theme**
Push the Frontier

**Team**
{{TEAM_NAME}} (solo)

**Summary** (100–200 words; this draft is 160 words before `{{HEADLINE_SHORT}}` is filled in, so keep that under 35 words)

fortyshots is an offline AI lab that decides which expensive experiment to run next. Meta's open-weights Muse Glimmer 30B runs on a 24 GB MacBook through llama.cpp, with no API key and no cloud. One model plays four roles: a director that explores 500 metal-organic frameworks with tools, a proposer that commits to picks with probabilities, a critic that reads its own plots through vision, and a reporter whose every number is checked against the run log. The agent gets 40 lookups of hidden DFT labels from Meta FAIR's ODAC23 to find frameworks that bind CO2 more strongly than water, which is why most sorbents fail in humid air. Its cheap physics simulator ranks the pool no better than chance overall but is enriched at the extreme, and the agent must check it against the labels it buys. Everything was pre-registered and scored blind against random choice, a random-forest active learner and the strongest baseline, greedy on the simulator: {{HEADLINE_SHORT}}. Every campaign replays in the browser, and `./lab verify` recomputes every published number without a model.

**Public repository**
https://github.com/print-EddyMa/fortyshots

**Live site**
https://print-EddyMa.github.io/fortyshots/

**Demo video** (3:00 or less; unlisted on an existing account; check that the link plays while logged out)
{{VIDEO_URL}}

**Meta models and tools used**
- Muse Glimmer 30B (open weights, Apache-2.0), run on our own hardware: MacBook Pro M4 Pro 24 GB, llama.cpp `llama-server`, Meta's `Q4_K_M` GGUF plus the `mmproj` vision projector. Every role in the agent is Glimmer: native tool calls, json_schema output, vision on its own plots, cited reports. {{DFLASH_SENTENCE}}
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
`{{COVER_IMAGE_PATH}}` (1200×630; the website build can generate one, see `web/package.json` script `og`)

**Anything else for judges** (optional field, if the form has one)
Headline numbers come with 95% intervals and name both baselines. The results page also shows where the agent lost or tied, the leak probe, and the physics-honesty panel. Limitations: the labels are existing DFT reference values, the candidates are known MOFs (picks, not discoveries), and agent-proposed variants are unvalidated.

---

Before pressing submit:
- [ ] Every `{{...}}` on this page is filled and matches `results/results.json`.
- [ ] Repository is public; tags `hackathon-start` and `prereg-v1` are pushed.
- [ ] Site loads at 390 px and 1440 px wide.
- [ ] Video link plays in a private browser window.
- [ ] Track is set to Indie.
