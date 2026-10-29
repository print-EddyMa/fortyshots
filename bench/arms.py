"""Baseline arms A0-A3: 40 DFT lookups per campaign, bought in 4 rounds of 10.

A0 random      uniform without replacement.
A1 heuristic   eligible = open metal site and 3.3 <= PLD <= 6 A. Order: eligible first, then OMS
               without the PLD window, then the rest; within each group cheaper metal_cost_tier first,
               then higher metal-site density (metal atoms per nm^3), then a seeded random tie-break.
A2 MACE greedy ascending S_mace, seeded random tie-break.
A3 RF-UCB      round 1: 10 uniform random; rounds 2-4: RF (bench.surrogate, 500 trees, random_state =
               seed) on all bought labels, buy the 10 unbought MOFs with the lowest mean - 1.0 * std of
               predicted S.

Run: python -m bench.arms [--pools P0 P1 ...] [--seeds 50] [--out results/baselines.parquet]
Output (long format): arm, pool, seed, order (1..40), round (1..4), id, hit.
A2 and A3 refuse a pool with incomplete MACE values (MaceCoverageError) instead of ranking them as worst.
"""
import argparse
import sys
import time

import numpy as np
import pandas as pd

from bench import surrogate
from bench.common import ALL_POOLS, BATCH, BUDGET, MACE_COLS, ROOT, ROUNDS, MaceCoverageError, load_data

N_SEEDS = 50
KAPPA = 1.0
PLD_WINDOW = (3.3, 6.0)


def rng_for(arm, pool, seed):
    return np.random.default_rng([int(arm[1:]), int(pool[1:]), seed, 20261029])


def a0_random(pool, rng):
    return list(pool.id.to_numpy()[rng.permutation(len(pool))[:BUDGET]])


def a1_heuristic(pool, rng):
    oms = pool.oms.fillna(False).astype(bool).to_numpy()
    pld = pool.pld_A.to_numpy(dtype=float)
    window = oms & (pld >= PLD_WINDOW[0]) & (pld <= PLD_WINDOW[1])
    group = np.where(window, 0, np.where(oms, 1, 2))
    tier = pool.metal_cost_tier.fillna(3).to_numpy(dtype=float)
    density = (pool.n_metal_atoms / pool.volume_A3 * 1000.0).fillna(0).to_numpy(dtype=float)
    order = np.lexsort((rng.random(len(pool)), -density, tier, group))
    return list(pool.id.to_numpy()[order[:BUDGET]])


def a2_mace_greedy(pool, rng):
    s = pool.S_mace.to_numpy(dtype=float)
    order = np.lexsort((rng.random(len(pool)), np.nan_to_num(s, nan=np.inf)))
    return list(pool.id.to_numpy()[order[:BUDGET]])


def a3_rf_ucb(pool, rng, seed):
    ids = pool.id.to_numpy()
    bought = list(rng.permutation(len(pool))[:BATCH])
    for _ in range(ROUNDS - 1):
        mask = np.zeros(len(pool), bool)
        mask[bought] = True
        train, test = pool[mask], pool[~mask]
        mean, std = surrogate.fit_predict(train, train.S, test, seed)
        acq = surrogate.ucb_rank(mean, std, KAPPA)
        pick = np.lexsort((rng.random(len(test)), acq))[:BATCH]
        bought += list(np.flatnonzero(~mask)[pick])
    return list(ids[bought])


def run_arm(arm, pool_df, seed):
    if arm in ("A2", "A3"):
        missing = int((~np.isfinite(pool_df[MACE_COLS].to_numpy(dtype=float)).all(axis=1)).sum())
        if missing:
            raise MaceCoverageError(f"{arm} on {pool_df.pool.iloc[0]}: {missing} of {len(pool_df)} MOFs lack MACE values")
    rng = rng_for(arm, pool_df.pool.iloc[0], seed)
    if arm == "A0":
        return a0_random(pool_df, rng)
    if arm == "A1":
        return a1_heuristic(pool_df, rng)
    if arm == "A2":
        return a2_mace_greedy(pool_df, rng)
    if arm == "A3":
        return a3_rf_ucb(pool_df, rng, seed)
    raise ValueError(arm)


def run_all(data, pools, seeds, arms=("A0", "A1", "A2", "A3"), log=print):
    rows = []
    for pool_name in pools:
        pool_df = data.pool(pool_name)
        hit = dict(zip(pool_df.id, pool_df.hit.astype(bool)))
        for arm in arms:
            t0 = time.time()
            for seed in range(seeds):
                for k, i in enumerate(run_arm(arm, pool_df, seed)):
                    rows.append((arm, pool_name, seed, k + 1, k // BATCH + 1, i, hit[i]))
            log(f"{pool_name} {arm}: {seeds} seeds in {time.time() - t0:.1f} s")
    return pd.DataFrame(rows, columns=["arm", "pool", "seed", "order", "round", "id", "hit"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pools", nargs="+", default=ALL_POOLS)
    ap.add_argument("--seeds", type=int, default=N_SEEDS)
    ap.add_argument("--arms", nargs="+", default=["A0", "A1", "A2", "A3"])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "results" / "baselines.parquet"))
    a = ap.parse_args([] if argv is None else argv)
    df = run_all(load_data(a.data), a.pools, a.seeds, a.arms)
    df.to_parquet(a.out, index=False)
    print(df.groupby(["pool", "arm", "seed"]).hit.sum().groupby(["pool", "arm"]).mean().unstack().round(2))


if __name__ == "__main__":
    main(sys.argv[1:])
