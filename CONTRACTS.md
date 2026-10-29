# fortyshots interface contracts

This file is binding for every component. If you need to change a contract, change it here first, in the same commit as the code.

## Science definitions

- **Universe.** ODAC23 IS2R pristine MOFs that have both CO2 and H2O single-adsorbate DFT labels, with at most 200 atoms including the adsorbate. The train split has 3,026 MOFs, 85 of them hits.
- **Pools.** Six disjoint pools of 500 MOFs, made with seed 20261029: `P0` is for development and prompt tuning only; `P1` to `P5` are test pools. The val split forms pool `V` (84 MOFs, 3 hits) and is used only for descriptive checks.
- **DFT label.** The minimum `y_relaxed` over ODAC placements, for each gas, in eV (PBE+D3). This gives `E_CO2` and `E_H2O`.
- **Hit.** `E_CO2 < -0.5` and `E_CO2 < E_H2O`.
- **DAC score.** Lower is better: `S = E_CO2 + max(0, E_CO2 - E_H2O + 0.1)`.
- **Cheap oracle.** MACE-MP-0 medium, float32, no D3, single points at the ODAC initial placements (at most 5 per gas). `E_ads = E(MOF+gas) - E(MOF) - E(gas)`. The cheap value is the minimum over placements. `S_mace` is the DAC score of the cheap values.
- **Budget.** 40 DFT lookups per campaign, bought in 4 rounds of 10. The first agent arms use these values; they are frozen in `PREREG.md`.

## Data files (committed, `data/`)

| File | Columns | Who may read it |
|---|---|---|
| `universe.parquet` | id, split, pool, natoms_framework, n_co2_placements, n_h2o_placements | anyone |
| `labels.parquet` | id, E_CO2, E_H2O, hit, E_CO2_init, E_H2O_init | **only** `fortyshots/tools.py` (inside `request_dft`) and `bench/`. Never exposed to the LLM except the rows it bought. `E_*_init` is never exposed to the agent at all. |
| `id_map.parquet` | id, refcode | `bench/` and the leak probe only. Public by design, for reproducibility. |
| `mace_cache.parquet` | id, E_CO2_mace, E_H2O_mace, S_mace, co2_best_placement, h2o_best_placement, co2_best_ads_pos (JSON string, 3x3 Å) | anyone |
| `descriptors.parquet` | id, formula, metals (comma-separated symbols), n_metal_atoms, natoms, volume_A3, density_gcm3, oms (bool), lcd_A, pld_A, asa_m2g, void_fraction, metal_cost_tier (1 cheap, 2 mid, 3 costly) | anyone |
| `structures/<id>.json` | `{id, cell[3][3], numbers[], ads[] (indices of the CO2 atoms), pos[][3] (ODAC initial), pos_relaxed[][3] (DFT relaxed, minimum-image unwrapped)}`; the best-DFT CO2 placement | the web viewer; the agent never reads these |

IDs look like `m` plus 7 hex characters, for example `m3fa9c2e`. They are salted SHA-256 hashes of the CSD refcode. No refcode may appear in a prompt, a cassette, a run file or the web bundle. Only `id_map.parquet` may contain refcodes.

## Run files (`runs/<run_id>/`)

- **`run_id`.** Format `{arm}-{pool}-s{seed}-{YYYYMMDDHHMM}`, for example `A5-P1-s0-202610312105`. Mock runs carry the prefix `mock-`.
- **`events.jsonl`.** An append-only ledger, one JSON object per line.
- **`summary.json`.** Written at the end of a run. It equals the payload of the final `summary` event.
- **`img/*.png`.** Images the agent saw: 512 px PNGs of renders and calibration plots.
- **`cassette.jsonl`.** One `{key: sha256(canonical request), request_meta, response}` per model call. Images are stored as hashes, not base64. Cassettes are not copied to the website.
- **`runs/index.json`.** A list of `{run_id, arm, pool, seed, mode, hits, spent, duration_s, started, model}`. `fortyshots index` regenerates it.

### Event envelope

```
{seq:int, t:"ISO8601", run_id, round:int (0 = setup), role: "director"|"proposer"|"critic"|"reporter"|"chemist"|"system"|"human",
 kind, payload:{...}, call_id: str|null, tokens: {prompt, completion, reasoning}|null,
 timings: {prompt_n, prompt_per_second, predicted_n, predicted_per_second, cache_n}|null, dur_ms: int|null}
```

### Event kinds and payloads

| kind | payload |
|---|---|
| `header` | `{arm, pool, seed, budget, rounds, batch, goal, mode: "recorded"\|"live"\|"mock", model: {name, gguf, sha256, quant, server_build, flags[]}, machine: {chip, ram_gb, os}, network: "on"\|"off"\|"unknown", prereg_sha, git_sha}` |
| `llm_call` | `{model, reasoning_strength, n_messages, n_images, finish_reason, schema: str\|null}`; `tokens` and `timings` are filled |
| `reasoning` | `{text}`: the model's `reasoning_content`, same `call_id` as its `llm_call` |
| `message` | `{text}`: the model's visible content, if any |
| `tool_call` | `{name, args}`; `call_id` like `c0012` |
| `tool_result` | `{name, ok: bool, summary: str (one line, ≤120 chars), result: any (may be truncated), image: "img/....png"\|null}`; same `call_id` |
| `proposal` | `{picks: [{id, p_hit: float, rationale, evidence: [call_id]}]}` |
| `critique` | `{visual_rho: float\|null, vetoes: [{id, reason, evidence: [call_id]}], notes}` |
| `human` | `{action: "approve"\|"reject"\|"auto", ids: [], note}` |
| `dft_order` | `{ids, rationale, spent, remaining}` |
| `dft_result` | `{results: [{id, E_CO2, E_H2O, hit}], hits_total, spent}` |
| `calibration` | `{n, spearman, auc, image}` |
| `mutation` | `{parent, spec, status, note}` (prospective mode) |
| `report` | `{text}` (cites `[c0012]` call ids) |
| `claimcheck` | `{n_claims, verified, flagged: [{claim, reason}]}` |
| `error` | `{where, message, recovered: bool}` |
| `summary` | `{arm, pool, seed, hits, spent, picks: [ids in purchase order], hit_flags: [bool], lookups_to_first_hit, brier: float\|null, p_hits: [float\|null], duration_s, aborted: bool, autofill: int}` |

## Agent tools (the A5 set; A6 drops `fit_surrogate`; A4 has no feedback tools)

| Tool | Args | Returns |
|---|---|---|
| `query_pool` | `filters: {metal_in?, metal_not_in?, oms?, pld_min?, pld_max?, lcd_min?, lcd_max?, cost_tier_max?, exclude_bought?: bool}`, `sort_by: one of S_mace, E_CO2_mace, pld_A, lcd_A, density_gcm3, void_fraction, asa_m2g`, `ascending: bool`, `limit ≤ 25` | rows of id plus the descriptors plus the cheap MACE values |
| `get_descriptors` | `ids ≤ 25` | descriptor rows |
| `cheap_score` | `ids ≤ 25` | `{id, E_CO2_mace, E_H2O_mace, S_mace}`: raw values with no error card |
| `fit_surrogate` | `kappa: float = 1.0`, `top_k ≤ 25` | RF (500 trees, fixed seed) on bought labels, fit on descriptors plus MACE; returns top-k unbought rows by `mean − kappa·std` of predicted S, with mean and std. Refuses if fewer than 5 labels have been bought. |
| `calibrate_oracle` | none | `{n, spearman, auc}` of MACE S against the DFT S over the bought labels, plus a rendered parity PNG |
| `render_structure` | `id` | 512 px PNG of the framework with CO2 at the best MACE placement |
| `request_dft` | `ids, rationale` | Budget-enforced. Returns `E_CO2`, `E_H2O` and `hit` for those ids only. Hard refusal beyond 40. |

## Results file (`results/results.json`, written by `bench/`)

```
{generated, status: "mock"|"pilot"|"final", prereg_sha, budget, base_rate,
 pools: [{pool, n, hits}],
 arms: [{arm, label, kind: "baseline"|"llm", n_runs, hits_mean, hits_ci: [lo, hi], enrichment, lookups_per_hit,
         first_hit_mean, curve: [41 floats, mean hits after k lookups], curve_lo: [...], curve_hi: [...]}],
 tests: [{name, diff, ci: [lo, hi], p}],
 calibration: {brier, n, bins: [{p_mean, hit_rate, n}]} | null,
 critic: {visual_rho_mae, n} | null,
 honesty: {mace_auc, mace_auc_ci, gas_relax_auc, dft_init_auc, mace_mae_dft_geom, eqv2_mae_dft_geom, notes},
 leak: {L_auc, L2_auc} | null,
 claims: {n, verified, flagged} | null,
 ceilings: [{pool, oracle_hits, dft_init_hits, random_expected}]}
```

`results/pools.json`: `{pools: {P0..P5: [{id, S_mace, E_CO2_mace, E_H2O_mace, pld_A, lcd_A, oms, metals, cost_tier, hit, E_CO2, E_H2O}]}}`. The browser uses it for the A0–A2 re-roll. These labels are public; the browser is not the agent.

## CLI (`fortyshots ...`, Python package `fortyshots/`)

| Command | What it does |
|---|---|
| `fortyshots mock-llm --port 8080` | Deterministic fake llama-server (OpenAI-compatible, same response shape, including `reasoning_content` and `timings`) |
| `fortyshots doctor --base-url http://127.0.0.1:8080 --out doctor/report.json` | Model round trips: tools, json_schema, vision, reasoning budget, tok/s, cold and warm prefill |
| `fortyshots run --arm A5 --pool P1 --seed 0 [--base-url URL] [--mode recorded\|live\|mock] [--action-mode native\|schema] [--max-minutes 45] [--runs-dir runs]` | One campaign; arms are A4, A5, A6, L and L2 |
| `fortyshots record mac/plans/<name>.json [--base-url URL]` | Runs a list of `{arm, pool, seed}`; skips finished run ids; resumes unfinished ones |
| `fortyshots serve --port 7777 [--runs-dir runs] [--site docs]` | Live mode: static site plus `GET /api/runs`, `GET /api/runs/<id>/events` (SSE tail), `POST /api/runs/<id>/human` |
| `fortyshots index` | Rebuilds `runs/index.json` |
| `fortyshots verify` | Recomputes every number in `results/results.json` from `runs/` and `data/` with no model, and diffs the result against the committed file |

## llama-server facts (Muse Glimmer 30B GGUF, llama.cpp with `--jinja`)

- **Endpoint.** `POST /v1/chat/completions`. Request fields: `messages`, `tools`, `response_format: {type: "json_schema", json_schema: {name, schema, strict: true}}`, `chat_template_kwargs: {reasoning_strength: "low"|"medium"|"high"|"xhigh"}`, `max_tokens`, `temperature`.
- **Response.** `choices[0].message` has `{content, reasoning_content, tool_calls: [{id, type: "function", function: {name, arguments (JSON string)}}]}`, plus `finish_reason`, `usage`, and `timings: {prompt_n, prompt_ms, prompt_per_second, predicted_n, predicted_ms, predicted_per_second, cache_n}`.
- **Images.** Passed as user content parts `{type: "image_url", image_url: {url: "data:image/png;base64,..."}}`. They need `--mmproj`.
- **Reasoning cannot be disabled.** An empty `content` with `finish_reason=length` counts as a tool error.
- **The client refuses any non-loopback base URL.**

## Web (`web/`, Vite + React + TypeScript)

- **Build output.** `docs/` at the repo root. GitHub Pages serves it from branch `main`, folder `/docs`.
- **Routing and base.** Hash routing (`#/console/<run_id>`), and Vite `base: './'`.
- **What the build copies.** `runs/` (all files except `cassette.jsonl`), `results/`, and `data/structures/`, into `docs/data/...`.
- **Live mode.** `?live=1` reads `/api/...` from the live server instead of static files.

## Addenda (Oct 29, from the agent-core build)

- **`request_dft`.** The result also carries `S`, `hits_new`, `hits_total`, `spent`, `remaining` and `already_bought_not_charged`. Table-shaped tool results are `{columns, rows}`.
- **`render_structure`.** Uses only the framework atoms from `structures/<id>.json` plus `co2_best_ads_pos` from the MACE cache. The stored best-DFT CO2 placement is never shown to the agent.
- **Extra payload keys.**
  - `llm_call.n_tool_calls`
  - `proposal.stage` (`initial`, `revised` or `probe`) and `proposal.replaced`
  - `critique.vision`
  - `report.source` (`model` or `template`) and `report.written_by`
  - `header.config`
  - `summary.run_id`, `summary.mode`, `summary.model`
  - For L and L2: `summary.ranking` and `summary.leak: {auc, n, n_hits, n_scored}`
- **Reporting phase.** Autofill, the report and the claim check are logged at round `rounds + 1`.
- **Live mode.** The live server uses two files, `runs/<id>/pending.json` and `runs/<id>/human.jsonl`.
  - `POST /api/runs/<id>/human` takes `{action, ids, note, round?}`.
  - Extra GET endpoints: `/api/runs/<id>/pending`, `/api/runs/<id>/summary`, `/api/runs/<id>/img/<name>` and `/api/network`.
  - The static mount is `/data/runs`.
- **Extra `run` flags.** `--resume`, `--run-id`, `--rounds N` (the budget becomes 10·N), `--no-vision`, `--shared-tools`, `--cassette off|record|replay`, `--cassette-from`, `--chemist`, `--data-dir` and `--doctor-report`.
- **Plan files.** Either `{defaults?, runs: [{arm, pool, seed, ...}]}` or a bare list.
- **Leak AUC.** Compute it from `summary.leak` or `summary.ranking`, not from `picks`.
- **`runs/index.json` entries.** Optional fields `showcase: bool` and `goal: str`. The showcase run is the median A5 run on P1, chosen by the PREREG rule; if no run is marked, the web falls back to the first A5 run on P1.
- **`results/pools.json` rows.** Optional field `metal_density` (`n_metal_atoms / volume_A3 × 1000`). Rows may use either `cost_tier` or `metal_cost_tier`.
- **`mace_cache.parquet` placement names.** Names carry no refcode prefix (for example `CO2_random_2`).
- **`co2_best_ads_pos`.** Carbon first, with the molecule made whole across periodic boundaries.
- **`structures/<id>.json`.** The adsorbate is also made whole in `pos`, and `pos_relaxed` is unwrapped against it.
- **L and L2 summaries.** Also carry `scores` (id → model score), so `verify` can recompute `leak.auc`.
- **`results.json` `tests[]` entries.** May carry an optional `ci_note`.
- **`fit_surrogate`.** Uses the same code as arm A3 (`bench/surrogate.py`).
- **Benchmark integrity (`bench/`).** Optional `results.json` fields: `pools[].mace_coverage` (fraction of the pool with finite MACE values; `bench.results` and `bench.verify` refuse any analysed pool below 1.0), `arms[].harness_share` (LLM arms: pool-weighted mean share of purchases in `summary.harness_picks`, missing key = 0) and `critic.n_text` (text-only critiques, not scored; `critic.visual_rho_mae` uses `critique.vision == true` only and is null when there are none). `prereg_sha` is the commit of tag `prereg-v1` (`git rev-parse prereg-v1^{commit}`) or `"unfrozen"`. Purchases are rebuilt from `dft_order`/`dft_result` events, never read from `summary.picks`; any summary/ledger/header/pool/label disagreement fails `verify`, and runs whose `header.budget` is not 40 are excluded with a logged reason.
- **Harness picks (`summary`).** `harness_picks: int` and `harness_pick_ids: [id]` (purchase order) count every bought MOF that the harness chose instead of the model: Proposer fallback picks (invalid batch after retries), live-mode replacements for human-rejected picks, and the end-of-run autofill. `autofill` still counts only the end-of-run fill. L and L2 carry `0` and `[]`.
- **Autofill rule.** The end-of-run autofill buys the unbought MOFs with the lowest `S_mace`, for every arm (it never calls `fit_surrogate`). Rationale in the `dft_order`: `autofill: <why>; N lookups filled from the S_mace ranking`.
- **Model-server failures.** A failed model call (read timeout, server unreachable after the client's own retries, HTTP error) is logged as `error {where: "<role>.llm", message, recovered, cause: "transport", attempt}` with the call's `call_id`, then retried after a backoff (10 s, doubling, max 120 s) and a `/health` wait. After 3 consecutive failures of one call the campaign logs `error {where: "llm_unavailable"}`, stops, autofills, writes the template report and its summary with `aborted: true`. On resume, `error` events that carry the call id of a call about to be sent are replayed as failed attempts of that call (including legacy ones without `cause`), so the call is simply sent again; they never cause a `LedgerDivergence`.
- **Context overflow and length cuts.** HTTP 400/413 whose body mentions the context (llama-server: "exceeds the available context size") raises `ContextOverflow`; it is logged as `error {cause: "context"}` and the call is retried once with a trimmed thread (older tool results cut to their first line, all images dropped). An empty answer with `finish_reason=length` is retried once the same way with `reasoning_strength` lowered one level (`xhigh→high→medium→low`), logged as `error {cause: "length"}` after the retry. A retry that overflows again counts as an empty answer (the role's normal invalid-output path).
- **Per-call timeout.** `max(300, prefill + max_tokens / decode_tok_s × 1.5)` seconds, where `prefill = 30 + (request chars / 3 + 1024 per image) / prefill_tok_s`. Speeds come from `--doctor-report` (`checks[throughput].detail.decode_tok_s` and the median cold `prompt_per_second`), else 8 and 100 tok/s. `FORTYSHOTS_TIMEOUT_S` replaces the formula. `header.config.llm` records `{timeout_floor_s, decode_tps, prefill_tps, timeout_override_s, attempts}`.
- **A4 batch.** The Proposer's `rationale` has `maxLength` 140 (about 20 words) in A4 and its instruction ends with "Keep each rationale to 20 words or fewer." (other arms: 240).
- **Leak probe L scrubbing.** Everything the model wrote (reasoning, messages, error messages, the cassette) is rewritten before it reaches the disk: refcodes match case-insensitively and with up to two trailing digits (an exact `id_map` entry wins, else the base refcode's id), and any remaining token matching `[A-Z]{6}(\d{2})?` becomes `[refcode]`. Score keys are matched to MOFs the same way.
- **`header.prereg_sha`.** sha256 of `PREREG.md` at the repo root (the package's parent directory), independent of the working directory.
- **Mock test hooks.** `fortyshots mock-llm` reads `FORTYSHOTS_MOCK_N_CTX` (answer HTTP 400 "exceeds the available context size" above that many prompt tokens) and `FORTYSHOTS_MOCK_REASONING_TOKENS` (JSON `{strength: tokens}`, forces reasoning length so `finish_reason=length` can be simulated).
