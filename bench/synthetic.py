"""A small synthetic repo root (data/, research/out/, runs/) for tests and for exercising the pipeline
before real runs exist. Labels are random; nothing here is a measurement."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from bench.common import ALL_POOLS, BUDGET, dac_score, is_hit

METALS = ["Zn", "Cu", "Co", "Mg", "Al", "Cd", "Eu"]


def make_data(root, pool_size=120, seed=0):
    rng = np.random.default_rng(seed)
    root = Path(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    pools = [*ALL_POOLS, "V"]
    n = pool_size * len(pools)
    ids = [f"m{k:07x}" for k in rng.choice(16**7, n, replace=False)]
    pool = np.repeat(pools, pool_size)
    latent = rng.normal(size=n)
    co2 = -0.25 - 0.25 * latent + 0.1 * rng.normal(size=n)
    h2o = -0.4 + 0.2 * rng.normal(size=n)
    hit = is_hit(co2, h2o)
    pd.DataFrame({"id": ids, "split": np.where(pool == "V", "val", "train"), "pool": pool,
                  "natoms_framework": rng.integers(20, 200, n), "n_co2_placements": 5,
                  "n_h2o_placements": 5}).to_parquet(root / "data/universe.parquet", index=False)
    pd.DataFrame({"id": ids, "E_CO2": co2, "E_H2O": h2o, "hit": hit, "E_CO2_init": co2 + 0.2 * rng.normal(size=n),
                  "E_H2O_init": h2o + 0.2 * rng.normal(size=n)}).to_parquet(root / "data/labels.parquet", index=False)
    pd.DataFrame({"id": ids, "refcode": [f"REF{k:04d}" for k in range(n)]}).to_parquet(
        root / "data/id_map.parquet", index=False)
    e_co2 = co2 + 0.3 * rng.normal(size=n)
    e_h2o = h2o + 0.3 * rng.normal(size=n)
    pd.DataFrame({"id": ids, "E_CO2_mace": e_co2, "E_H2O_mace": e_h2o, "S_mace": dac_score(e_co2, e_h2o),
                  "co2_best_placement": "CO2_1", "h2o_best_placement": "H2O_1",
                  "co2_best_ads_pos": "[[0,0,0],[0,0,1.16],[0,0,-1.16]]"}).to_parquet(
        root / "data/mace_cache.parquet", index=False)
    metals = rng.choice(METALS, n)
    pld = rng.uniform(2, 9, n)
    pd.DataFrame({"id": ids, "formula": [f"{m}C8H4O4" for m in metals], "metals": metals,
                  "n_metal_atoms": rng.integers(1, 9, n), "natoms": rng.integers(20, 200, n),
                  "volume_A3": rng.uniform(500, 4000, n), "density_gcm3": rng.uniform(0.5, 2.5, n),
                  "oms": rng.random(n) < 0.4 + 0.3 * (latent > 1), "lcd_A": pld + rng.uniform(0, 2, n),
                  "pld_A": pld, "asa_m2g": rng.uniform(0, 3000, n), "void_fraction": rng.uniform(0.2, 0.8, n),
                  "metal_cost_tier": rng.integers(1, 4, n)}).to_parquet(root / "data/descriptors.parquet", index=False)
    out = root / "research/out"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "relax_mace.jsonl", "w") as fh:
        for k in range(12):
            y = -0.9 if k < 4 else -0.2
            fh.writelines(json.dumps({"mof": f"X{k}", "gas": gas, "pred": float(rng.normal(-0.2, 0.2)), "y": yy}) + "\n" for gas, yy in (("CO2", y), ("H2O", -0.3)))
    with open(out / "val_mace-mp0.jsonl", "w") as fh:
        for k in range(20):
            y = float(rng.normal(-0.3, 0.2))
            fh.write(json.dumps({"name": f"v{k}", "gas": "CO2" if k % 2 else "H2O", "y_relaxed": y,
                                 "eqv2_odac_sp": y + 0.1, "pred": y - 0.15}) + "\n")


def write_run(root, arm, pool, seed, picks, hit_of, p_hits=None, mock=True, critiques=(), claims=None,
              stamp="202610301200", budget=BUDGET, git_sha="0" * 40, harness_picks=None, extra=None,
              vision=True):
    """A run directory shaped like a real campaign's: header, one dft_order + dft_result pair per batch of
    10 purchases, calibration/critique pairs, claimcheck and a final summary event equal to summary.json.
    Leak probes (L, L2) buy nothing, so they get no DFT events."""
    run_id = f"{'mock-' if mock else ''}{arm}-{pool}-s{seed}-{stamp}"
    d = Path(root) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    probe = arm in ("L", "L2")
    flags = [bool(hit_of[i]) for i in picks]
    first = next((k + 1 for k, f in enumerate(flags) if f), None)
    summary = {"run_id": run_id, "arm": arm, "pool": pool, "seed": seed, "mode": "mock" if mock else "recorded",
               "hits": sum(flags), "spent": 0 if probe else len(picks), "picks": list(picks), "hit_flags": flags,
               "lookups_to_first_hit": first, "brier": None,
               "p_hits": p_hits if p_hits is not None else [None] * len(picks), "duration_s": 60.0,
               "aborted": False, "autofill": 0}
    if harness_picks is not None:
        summary["harness_picks"] = len(harness_picks)
        summary["harness_pick_ids"] = list(harness_picks)
    summary.update(extra or {})
    events = []

    def emit(rnd, role, kind, payload):
        events.append({"seq": len(events), "t": "2026-10-30T12:00:00Z", "run_id": run_id, "round": rnd,
                       "role": role, "kind": kind, "payload": payload})

    emit(0, "system", "header", {"arm": arm, "pool": pool, "seed": seed, "budget": budget, "rounds": budget // 10,
                                 "batch": 10, "mode": summary["mode"], "prereg_sha": "test0000",
                                 "git_sha": git_sha})
    cal = dict(enumerate(critiques, start=2))
    for k in range(0, 0 if probe else len(picks), 10):
        rnd = k // 10 + 1
        if rnd in cal:
            visual_rho, spearman = cal.pop(rnd)
            emit(rnd, "system", "calibration", {"n": k, "spearman": spearman, "auc": 0.5, "image": None})
            emit(rnd, "critic", "critique", {"visual_rho": visual_rho, "vetoes": [], "notes": "", "vision": vision})
        batch = picks[k:k + 10]
        emit(rnd, "system", "dft_order", {"ids": batch, "rationale": "", "spent": k + len(batch),
                                          "remaining": budget - k - len(batch)})
        emit(rnd, "system", "dft_result", {
            "results": [{"id": i, "E_CO2": -0.6, "E_H2O": 0.0, "hit": bool(hit_of[i])} for i in batch],
            "hits_total": sum(flags[:k + len(batch)]), "spent": k + len(batch)})
    if claims:
        emit(4, "system", "claimcheck", claims)
    emit(5, "system", "summary", summary)
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "summary.json").write_text(json.dumps(summary))
    return run_id


def make_mock_runs(root, data, pools=("P1", "P2"), seeds=2, seed=1):
    """Mock A4/A5/A6 runs: greedy-by-label-with-noise picks so the LLM arms differ from random. In every
    A5/A6 run the last (seed + 1) * 5 purchases are logged as harness picks (end-of-run autofill)."""
    rng = np.random.default_rng(seed)
    hit_of = dict(zip(data.labels.id, data.labels.hit.astype(bool)))
    for arm, noise in (("A4", 1.0), ("A5", 0.3), ("A6", 0.5)):
        for pool in pools:
            df = data.pool(pool)
            for s in range(seeds):
                score = df.S.to_numpy() + noise * rng.normal(size=len(df))
                picks = list(df.id.to_numpy()[np.argsort(score)[:BUDGET]])
                p_hits = [float(np.clip(0.5 - 0.5 * score[k], 0, 1)) for k in np.argsort(score)[:BUDGET]]
                write_run(root, arm, pool, s, picks, hit_of, p_hits=p_hits if arm != "A4" else None,
                          critiques=[(0.1, 0.05), (0.2, 0.3)] if arm != "A4" else (),
                          harness_picks=picks[BUDGET - (s + 1) * 5:] if arm != "A4" else None,
                          claims={"n_claims": 10, "verified": 9, "flagged": [{"claim": "x", "reason": "y"}]})
    ranking = rng.permutation(data.pool("P1").id.to_numpy()).tolist()
    write_run(root, "L", "P1", 0, ranking[:BUDGET], hit_of, extra={"ranking": ranking})
