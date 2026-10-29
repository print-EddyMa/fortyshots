"""Recompute results/results.json and results/pools.json from runs/, data/ and results/baselines.parquet
(no model calls) and diff them against the committed files. Also re-runs the baseline arms A0-A2 in full
and a sample of A3 campaigns, and checks they reproduce results/baselines.parquet exactly.

usage: python -m bench.verify [--root .] [--a3-sample 2]   (exit 1 on any mismatch)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from bench import arms
from bench.common import ROOT, TEST_POOLS, load_data
from bench.results import compute
from bench.schema import validate_pools, validate_results

FLOAT_TOL = 2e-4  # values are rounded to 4 decimals; allow one rounding flip
IGNORE = {"generated"}


def diff(a, b, path="", out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in IGNORE and not path:
                continue
            if k not in a or k not in b:
                out.append(f"{path}/{k}: missing in {'recomputed' if k not in a else 'committed'}")
            else:
                diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, bool) or isinstance(b, bool) or not (
            isinstance(a, (int, float)) and isinstance(b, (int, float))):
        if type(a) is not type(b) or a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    elif abs(a - b) > FLOAT_TOL:
        out.append(f"{path}: {a} != {b}")
    return out


def check_baselines(root, a3_sample):
    base = pd.read_parquet(root / "results" / "baselines.parquet")
    data = load_data(root / "data")
    problems = []
    for pool in sorted(base.pool.unique()):
        pool_df = data.pool(pool)
        for arm in ["A0", "A1", "A2", "A3"]:
            sub = base[(base.pool == pool) & (base.arm == arm)]
            seeds = sorted(sub.seed.unique())
            if arm == "A3":
                seeds = seeds[:a3_sample] if pool == "P1" else []
            for seed in seeds:
                want = sub[sub.seed == seed].sort_values("order").id.tolist()
                if arms.run_arm(arm, pool_df, int(seed)) != want:
                    problems.append(f"baselines {arm} {pool} seed {seed}: purchase sequence differs")
        hit = dict(zip(pool_df.id, pool_df.hit.astype(bool)))
        sub = base[base.pool == pool]
        if (sub.id.map(hit) != sub.hit).any():
            problems.append(f"baselines {pool}: hit column differs from labels")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--a3-sample", type=int, default=2)
    a = ap.parse_args([] if argv is None else argv)
    root = Path(a.root)
    t0 = time.time()
    committed = json.loads((root / "results" / "results.json").read_text())
    committed_pools = json.loads((root / "results" / "pools.json").read_text())
    status = committed.get("status")
    results, pools, _ = compute(root, include_mock=status == "mock",
                                status=status if status in ("pilot", "final") else None,
                                pools=[p["pool"] for p in committed.get("pools", [])] or TEST_POOLS)
    problems = validate_results(committed) + validate_pools(committed_pools)
    problems += diff(results, committed) + diff(pools, committed_pools, "pools.json")
    problems += check_baselines(root, a.a3_sample)
    for p in problems[:50]:
        print("MISMATCH", p)
    print(f"verify: {len(problems)} mismatches in {time.time() - t0:.1f} s")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
