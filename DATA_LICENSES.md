# Data and model licenses

What fortyshots uses that it did not write, under which terms, and what we changed. Our own code is Apache-2.0 ([LICENSE](LICENSE)).

## ODAC23 (Open DAC 2023), Meta FAIR and Georgia Tech

- **Source:** `https://dl.fbaipublicfiles.com/dac/datasets/odac23_is2r.tar.gz` (IS2R split), downloaded without an account.
- **License:** Creative Commons Attribution 4.0 International (CC-BY-4.0).
- **Citation:** Anuroop Sriram, Sihoon Choi, Xiaohan Yu, Logan M. Brabson, Abhishek Das, Zachary Ulissi, Matt Uyttendaele, Andrew J. Medford, David S. Sholl. "The Open DAC 2023 Dataset and Challenges for Sorbent Discovery in Direct Air Capture." *ACS Central Science* 10, 923–941 (2024). arXiv:2311.00341.
- **Changes we made** (CC-BY requires us to say so):
  - Kept only pristine MOFs that have both a CO2 and an H2O single-adsorbate label and at most 200 atoms including the adsorbate (3,026 train-split MOFs and 84 val-split MOFs).
  - Reduced each MOF and gas to the minimum `y_relaxed` over ODAC's placements (`data/labels.parquet`), and derived a hit flag and a score from it (definitions in [CONTRACTS.md](CONTRACTS.md)).
  - Replaced CSD refcodes with salted SHA-256 IDs everywhere except `data/id_map.parquet`, which keeps the mapping public for reproducibility.
  - Stored one framework geometry and the best-DFT CO2 placement per MOF as JSON (`data/structures/`), with initial and DFT-relaxed adsorbate positions unwrapped by minimum image.
  - Computed new label-free descriptors (Zeo++ pore geometry, metal composition, a metal-cost tier) and MACE-MP-0 single-point energies on ODAC's initial placements (`data/descriptors.parquet`, `data/mace_cache.parquet`). These are our derived data, not part of ODAC23.
- The derived files in `data/` are shared under the same CC-BY-4.0 terms, with attribution to the ODAC23 authors above.

## Muse Glimmer 30B, Meta

- **Source:** `meta-models/Muse-Glimmer-30B-GGUF` on Hugging Face (ungated): `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf`, optionally `dflash-Muse-Glimmer-30B-Q4_K_M.gguf`. The smaller fallback quantizations come from `unsloth/Muse-Glimmer-30B-GGUF` (`UD-Q4_K_XL`, `UD-Q3_K_XL`), conversions of the same weights.
- **License:** Apache-2.0, plus Meta's Muse Glimmer Usage Policy:
  - License: https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/blob/main/LICENSE
  - Usage Policy: https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/blob/main/USAGE_POLICY.md
- **Weights are never redistributed in this repository.** `./lab download` fetches them from Hugging Face on the user's machine and checks the sha256 that Hugging Face publishes. `.gitignore` excludes `*.gguf`.
- **Model output is labelled.** Text the agent writes (reasoning, rationales, reports) appears in the ledger and on the site labelled "written by Muse Glimmer 30B" and is never presented as human-written (Usage Policy §3.5). Known failure modes are disclosed in the README (Usage Policy §4).

## MACE-MP-0, the cheap simulator

- **Model:** MACE-MP-0 "medium" foundation model, used through the `mace-torch` package (MIT).
- **License:** MIT.
- **Citation:** Ilyes Batatia et al. "A foundation model for atomistic materials chemistry." arXiv:2401.00096 (2023). The underlying architecture: Ilyes Batatia, Dávid Péter Kovács, Gregor Simm, Christoph Ortner, Gábor Csányi. "MACE: Higher Order Equivariant Message Passing Neural Networks for Fast and Accurate Force Fields." NeurIPS 2022.
- We commit only the energies we computed (`data/mace_cache.parquet`), not the model weights.

## EquiformerV2 ODAC23 checkpoint, Meta FAIR

- **Source:** `https://dl.fbaipublicfiles.com/dac/checkpoints_20240709/eqv2_31M.pt` (and the 20231116 release), used with `fairchem-core` 1.10 (MIT).
- **License:** the checkpoint files carry no stated license. We therefore **download them at run time and never commit or redistribute them.** Only energies we computed with them (prospective cross-checks and the honesty panel) are in the repository.
- **Citation:** Yi-Lun Liao, Brandon Wood, Abhishek Das, Tess Smidt. "EquiformerV2: Improved Equivariant Transformer for Scaling to Higher-Degree Representations." ICLR 2024 (arXiv:2306.12059); and the ODAC23 paper above.

## CoRE MOF 2019

{{CORE_MOF_USED}} At the time of writing our descriptors come from Zeo++ run on the ODAC23 geometries, not from CoRE MOF. If CoRE MOF data is added, cite: Yongchul G. Chung et al. "Advances, Updates, and Analytics for the Computation-Ready, Experimental Metal–Organic Framework Database: CoRE MOF 2019." *Journal of Chemical & Engineering Data* 64, 5985–5998 (2019); data on Zenodo under CC-BY-4.0. Otherwise delete this section.

## Metal prices for the cost tier

`metal_cost_tier` is an order-of-magnitude grouping informed by the USGS Mineral Commodity Summaries 2024 (a US government work, public domain). No USGS data is redistributed; only our three-tier grouping is.
