"""Recompute results/results.json and results/pools.json from runs/, data/ and results/baselines.parquet
(no model calls) and diff them against the committed files. Also:
- re-runs the baseline arms A0-A2 in full and a sample of A3 seeds on every analysed pool, and checks they
  reproduce results/baselines.parquet exactly;
- fails on every run whose summary disagrees with its ledger, header, pool or labels (bench.ingest);
- when tag prereg-v1 exists, fails on every analysed non-mock run whose header git_sha is not the tag
  commit or a descendant of it.
Refuses (exit 2) when an analysed pool lacks complete MACE values in data/mace_cache.parquet.

usage: python -m bench.verify [--root .] [--a3-sample 2]   (exit 1 on any mismatch, 2 on refusal)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from bench import arms
from bench.common import BASELINE_ARMS, ROOT, TEST_POOLS, MaceCoverageError, is_descendant, load_data, prereg_commit
from bench.ingest import LEAK_ARMS
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


def check_baselines(root, a3_sample, pools):
    """Re-run A0-A2 in full and the first a3_sample A3 seeds on every analysed pool."""
    base = pd.read_parquet(root / "results" / "baselines.parquet")
    data = load_data(root / "data")
    problems = []
    for pool in pools:
        pool_df = data.pool(pool)
        for arm in BASELINE_ARMS:
            sub = base[(base.pool == pool) & (base.arm == arm)]
            seeds = sorted(sub.seed.unique())
            if not seeds:
                problems.append(f"baselines {arm} {pool}: no runs in results/baselines.parquet")
                continue
            if arm == "A3":
                seeds = seeds[:a3_sample]
            for seed in seeds:
                want = sub[sub.seed == seed].sort_values("order").id.tolist()
                if arms.run_arm(arm, pool_df, int(seed)) != want:
                    problems.append(f"baselines {arm} {pool} seed {seed}: purchase sequence differs")
        hit = dict(zip(pool_df.id, pool_df.hit.astype(bool)))
        sub = base[base.pool == pool]
        if (sub.id.map(hit) != sub.hit).any():
            problems.append(f"baselines {pool}: hit column differs from labels")
    return problems


def check_runs(runs):
    """Every ledger/summary/header/label disagreement of every ingested run is a mismatch."""
    return [f"run {r.run_id}: {m}" for r in runs for m in r.mismatches]


def check_provenance(root, runs, pools):
    """Header git_sha of every analysed non-mock run must be the prereg-v1 commit or a descendant of it.
    Returns (problems, note); skipped (no problems) while the tag does not exist."""
    tag = prereg_commit(root)
    if tag is None:
        return [], "prereg-v1 tag not found: git_sha provenance check skipped"
    problems = []
    for r in runs:
        if r.mock or r.header.get("mode") == "mock" or (r.arm not in LEAK_ARMS and r.pool not in pools):
            continue
        sha = r.header.get("git_sha")
        if not is_descendant(root, tag, sha):
            problems.append(f"run {r.run_id}: header git_sha {sha!r} is not prereg-v1 ({tag[:12]}) or a descendant")
    return problems, f"git_sha provenance checked against prereg-v1 {tag[:12]}"


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
    analysed = [p["pool"] for p in committed.get("pools", [])] or TEST_POOLS
    try:
        results, pools, runs = compute(root, include_mock=status == "mock",
                                       status=status if status in ("pilot", "final") else None, pools=analysed)
    except MaceCoverageError as e:
        print(f"verify: refused: {e}")
        return 2
    problems = validate_results(committed) + validate_pools(committed_pools)
    problems += diff(results, committed) + diff(pools, committed_pools, "pools.json")
    problems += check_runs(runs)
    prov, note = check_provenance(root, runs, analysed)
    problems += prov
    problems += check_baselines(root, a.a3_sample, analysed)
    print(f"verify: {note}")
    for r in runs:
        if r.excluded and not r.mismatches:
            print(f"excluded {r.run_id}: {r.excluded}")
    for p in problems[:50]:
        print("MISMATCH", p)
    print(f"verify: {len(problems)} mismatches in {time.time() - t0:.1f} s")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
