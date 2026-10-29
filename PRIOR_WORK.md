# Prior work and what we reused

The hackathon rules ask what was built during the event. Everything in this repository was written from Oct 29, 2026 onward; the first commit is tagged `hackathon-start`. This page lists what we reused from others and what ideas we brought with us. The "Built during the hackathon" table in the [README](README.md) lists our own components with commit ranges.

## Reused as is (not our work)

| What | From | How we use it | Terms |
|---|---|---|---|
| Muse Glimmer 30B weights (GGUF, vision projector, DFlash drafter) | Meta | The agent's model, run locally; never modified or fine-tuned | Apache-2.0 + Usage Policy |
| llama.cpp `llama-server` | ggml-org | Serves Glimmer on the Mac (Homebrew build) | MIT |
| ODAC23 IS2R dataset | Meta FAIR, Georgia Tech | Hidden DFT labels and DFT-relaxed CO2 poses | CC-BY-4.0 |
| EquiformerV2 ODAC checkpoints, `fairchem-core` | Meta FAIR | Cross-check of prospective mutants; honesty panel | checkpoints: no stated license (downloaded at run time); code: MIT |
| MACE-MP-0 medium, `mace-torch` | Batatia et al. | The cheap simulator | MIT |
| Zeo++ (via pyzeo), pymatgen, ASE | their authors | Pore descriptors, structure handling | see [THIRD_PARTY.md](THIRD_PARTY.md) |
| 3Dmol.js, React, Geist fonts | their authors | Website | see [THIRD_PARTY.md](THIRD_PARTY.md) |

## Ideas we brought, no code

- **NC Recon** (the author's earlier project): the habits of bootstrap confidence intervals, abstaining or flagging rather than guessing, and a human review queue for anything unverified. No code from it is in this repository.
- **Research phase, Oct 29, 2026, in this hackathon:** before writing the agent we measured how well MACE-MP-0, MACE-MPA-0, Orb-v3 and EquiformerV2-ODAC rank ODAC23 binders. The scripts and raw outputs are in `research/`. Those measurements (for example MACE hit AUC 0.49 [0.40–0.58] on 200 training MOFs) shaped the design and are disclosed in [PREREG.md](PREREG.md) §10.

## Related work (for context; nothing reused)

LLM agents that call simulation tools for materials and chemistry exist. Our contribution is not that pattern but a pre-registered, budgeted, blind comparison against baselines, run on a laptop with an open model. Systems we are aware of, as described in their public papers:

- Catalyst-Agent (arXiv:2603.01311): an LLM agent with Meta's UMA / AdsorbML tools for catalyst screening, reporting trials per success. We report lookups per hit so the two are comparable in kind.
- DREAMS (arXiv:2608.20434): an LLM agent for MOF screening under wet flue gas. The closest problem setting to ours.
- MOFGen (arXiv:2504.14110), LEMO (arXiv:2607.10559), ChatMOF, ChemCrow, Coscientist, El Agente (arXiv:2505.02484): agents for MOF or chemistry tasks, mostly with closed models and task-completion metrics.
- Active learning with random forests and upper-confidence-bound acquisition is standard in materials screening; our A3 baseline is a plain version of it.

{{CHECK_RELATED_WORK}} Confirm each citation above against its abstract before submission, and remove any that cannot be confirmed.
