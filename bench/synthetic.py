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
              stamp="202610301200"):
    run_id = f"{'mock-' if mock else ''}{arm}-{pool}-s{seed}-{stamp}"
    d = Path(root) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    flags = [bool(hit_of[i]) for i in picks]
    first = next((k + 1 for k, f in enumerate(flags) if f), None)
    summary = {"arm": arm, "pool": pool, "seed": seed, "hits": sum(flags), "spent": len(picks), "picks": picks,
               "hit_flags": flags, "lookups_to_first_hit": first, "brier": None,
               "p_hits": p_hits if p_hits is not None else [None] * len(picks), "duration_s": 60.0,
               "aborted": False, "autofill": 0}
    events = [{"seq": 0, "t": "2026-10-30T12:00:00Z", "run_id": run_id, "round": 0, "role": "system",
               "kind": "header", "payload": {"arm": arm, "pool": pool, "seed": seed, "mode": "mock",
                                             "prereg_sha": "test0000"}}]
    for rnd, (visual_rho, spearman) in enumerate(critiques, start=2):
        events.append({"seq": len(events), "round": rnd, "role": "system", "kind": "calibration",
                       "payload": {"n": 10 * (rnd - 1), "spearman": spearman, "auc": 0.5, "image": None}})
        events.append({"seq": len(events), "round": rnd, "role": "critic", "kind": "critique",
                       "payload": {"visual_rho": visual_rho, "vetoes": [], "notes": ""}})
    if claims:
        events.append({"seq": len(events), "round": 4, "role": "system", "kind": "claimcheck", "payload": claims})
    events.append({"seq": len(events), "round": 4, "role": "system", "kind": "summary", "payload": summary})
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "summary.json").write_text(json.dumps(summary))
    return run_id


def make_mock_runs(root, data, pools=("P1", "P2"), seeds=2, seed=1):
    """Mock A4/A5/A6 runs: greedy-by-label-with-noise picks so the LLM arms differ from random."""
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
                          claims={"n_claims": 10, "verified": 9, "flagged": [{"claim": "x", "reason": "y"}]})
    ranking = rng.permutation(data.pool("P1").id.to_numpy()).tolist()
    run_id = write_run(root, "L", "P1", 0, ranking[:BUDGET], hit_of)
    path = Path(root) / "runs" / run_id / "summary.json"
    summary = json.loads(path.read_text())
    summary["ranking"] = ranking
    path.write_text(json.dumps(summary))
