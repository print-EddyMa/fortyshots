"""Build results/results.json and results/pools.json from data/, results/baselines.parquet and runs/.

usage: python -m bench.results [--include-mock] [--status pilot|final] [--pools P1 ... P5] [--root .]
"""
import argparse
import json
import math
import subprocess
import sys
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bench import stats
from bench.common import ALL_POOLS, ARM_LABELS, BASELINE_ARMS, BUDGET, LLM_ARMS, ROOT, TEST_POOLS, dac_score, load_data
from bench.ingest import LEAK_ARMS, critic_pairs, load_runs

TESTS = [("primary: A5 - A3 hits@40", "A5", "A3"), ("A5 - A0 hits@40", "A5", "A0"),
         ("A5 - A6 hits@40", "A5", "A6"), ("A3 - A0 hits@40", "A3", "A0")]
MIN_POOLS_FOR_POOL_RESAMPLING = 5
RESEARCH = {
    "gas_relax": "research/out/relax_mace.jsonl",
    "val": "research/out/val_mace-mp0.jsonl",
}


def seed_of(name):
    return zlib.crc32(name.encode())


def clean(x, nd=4):
    """Round floats, turn NaN/inf into None, numpy scalars into Python."""
    if isinstance(x, dict):
        return {k: clean(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v, nd) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(float(x)) else round(float(x), nd)
    return x


def baseline_runs(base, pools):
    """{arm: {pool: [hit flags per run in purchase order]}}"""
    out = {}
    b = base[base.pool.isin(pools)].sort_values(["arm", "pool", "seed", "order"])
    for (arm, pool, _), g in b.groupby(["arm", "pool", "seed"], sort=True):
        out.setdefault(arm, {}).setdefault(pool, []).append(g.hit.astype(bool).tolist())
    return out


def llm_runs(runs, pools):
    out = {}
    for r in runs:
        if r.arm in LLM_ARMS and r.pool in pools:
            out.setdefault(r.arm, {}).setdefault(r.pool, []).append(r.hit_flags)
    return {a: {p: out[a][p] for p in sorted(out[a])} for a in out}


def arm_entry(arm, kind, flags_by_pool, pool_hits, pool_n):
    curves = {p: np.array([stats.curve(f) for f in fl]) for p, fl in flags_by_pool.items()}
    hits = {p: c[:, -1] for p, c in curves.items()}
    first = {p: np.array([stats.first_hit(f) for f in fl], dtype=float) for p, fl in flags_by_pool.items()}
    resample = len(curves) >= MIN_POOLS_FOR_POOL_RESAMPLING
    s = seed_of(arm)
    boot = stats.hier_boot(curves, s, resample_pools=resample)
    lo, hi = stats.ci(boot)
    spent = sum(min(len(f), BUDGET) for fl in flags_by_pool.values() for f in fl)
    total_hits = sum(float(h.sum()) for h in hits.values())
    random_expected = np.mean([BUDGET * pool_hits[p] / pool_n[p] for p in curves])
    mean_curve = stats.pool_mean(curves)
    return {
        "arm": arm, "label": ARM_LABELS[arm], "kind": kind,
        "n_runs": int(sum(len(fl) for fl in flags_by_pool.values())),
        "hits_mean": float(mean_curve[-1]), "hits_ci": [float(lo[-1]), float(hi[-1])],
        "enrichment": float(mean_curve[-1] / random_expected) if random_expected else None,
        "lookups_per_hit": spent / total_hits if total_hits else None,
        "first_hit_mean": float(stats.pool_mean(first)),
        "curve": mean_curve.tolist(), "curve_lo": lo.tolist(), "curve_hi": hi.tolist(),
    }


def tests_block(arm_flags):
    out = []
    for name, a, b in TESTS:
        if a not in arm_flags or b not in arm_flags:
            continue
        ga = {p: np.array([stats.curve(f)[-1] for f in fl]) for p, fl in arm_flags[a].items()}
        gb = {p: np.array([stats.curve(f)[-1] for f in fl]) for p, fl in arm_flags[b].items()}
        shared = [p for p in ga if p in gb]
        if not shared:
            continue
        ga, gb = {p: ga[p] for p in shared}, {p: gb[p] for p in shared}
        draws = stats.hier_boot_diff(ga, gb, seed_of(name), resample_pools=len(shared) >= MIN_POOLS_FOR_POOL_RESAMPLING)
        diff, p = stats.perm_test(ga, gb, seed_of(name) + 1)
        lo, hi = stats.ci(draws)
        out.append({"name": name, "diff": diff, "ci": [float(lo), float(hi)], "p": p})
    return out


def calibration_block(runs, pools):
    p, y = [], []
    for r in runs:
        if r.arm == "A5" and r.pool in pools:
            for ph, h in zip(r.p_hits, r.hit_flags):
                if ph is not None:
                    p.append(float(ph))
                    y.append(bool(h))
    if not p:
        return None
    return {"brier": stats.brier(p, y), "n": len(p), "bins": stats.reliability(p, y)}


def critic_block(runs, pools):
    pairs = [pr for r in runs if r.arm in LLM_ARMS and r.pool in pools for pr in critic_pairs(r)]
    if not pairs:
        return None
    return {"visual_rho_mae": float(np.mean([abs(v - c) for v, c in pairs])), "n": len(pairs)}


def claims_block(runs, pools):
    checks = [r.claimchecks[-1] for r in runs if r.arm in LLM_ARMS and r.pool in pools and r.claimchecks]
    if not checks:
        return None
    return {"n": int(sum(c.get("n_claims", 0) for c in checks)),
            "verified": int(sum(c.get("verified", 0) for c in checks)),
            "flagged": int(sum(len(c.get("flagged") or []) for c in checks))}


def leak_block(runs, data):
    """Leak-probe AUC per probe arm, averaged over its runs: summary.leak.auc (the probe's own score-based AUC)
    when present, else the AUC of summary.ranking (CONTRACTS addenda); never the purchase order."""
    out = {}
    for arm in LEAK_ARMS:
        aucs = []
        for r in runs:
            if r.arm != arm:
                continue
            leak = r.summary.get("leak") or {}
            if leak.get("auc") is not None:
                aucs.append(float(leak["auc"]))
                continue
            order = r.summary.get("ranking")
            if not order:
                continue
            pool = data.pool(r.pool)
            rank = {i: k for k, i in enumerate(order)}
            score = np.array([rank.get(i, len(order)) for i in pool.id], dtype=float)
            if 0 < pool.hit.sum() < len(pool):
                aucs.append(stats.auc(score, pool.hit))
        out[f"{arm}_auc"] = float(np.mean(aucs)) if aucs else None
    return out if any(v is not None for v in out.values()) else None


def honesty_block(data, pools, root):
    frames = pd.concat([data.pool(p) for p in pools])
    m = frames.dropna(subset=["S_mace"])
    have_mace = len(m) > 0 and 0 < m.hit.sum() < len(m)
    mace_auc = stats.auc(m.S_mace, m.hit) if have_mace else None
    mace_ci = stats.auc_boot_ci(m.S_mace, m.hit, seed_of("mace_auc")) if have_mace else None
    s_init = dac_score(frames.E_CO2_init, frames.E_H2O_init)
    ok = np.isfinite(s_init)
    dft_init_auc = stats.auc(s_init[ok], frames.hit.to_numpy()[ok])

    rel = pd.read_json(root / RESEARCH["gas_relax"], lines=True)
    g = rel.pivot(index="mof", columns="gas", values=["pred", "y"]).dropna()
    rel_hit = (g[("y", "CO2")] < -0.5) & (g[("y", "CO2")] < g[("y", "H2O")])
    gas_relax_auc = stats.auc(dac_score(g[("pred", "CO2")], g[("pred", "H2O")]), rel_hit)

    val = pd.read_json(root / RESEARCH["val"], lines=True)
    mace_mae = float((val.pred - val.y_relaxed).abs().mean())
    eqv2_mae = float((val.eqv2_odac_sp - val.y_relaxed).abs().mean())
    n_pool = int(frames.id.nunique())
    notes = (
        f"mace_auc: hit AUC of S_mace (MACE-MP-0 medium float32, single points at ODAC initial placements) on "
        f"{len(m)}/{n_pool} MOFs of {', '.join(pools)} ({int(m.hit.sum())} hits); 95% CI from 2,000 MOF-level "
        f"bootstrap resamples. dft_init_auc: same score from ODAC23 DFT at the initial (unrelaxed) placements, "
        f"same pools (data/labels.parquet E_*_init). gas_relax_auc: MACE with only the gas molecule relaxed, "
        f"{len(g)} training MOFs ({int(rel_hit.sum())} hits), {RESEARCH['gas_relax']}. mace_mae_dft_geom and "
        f"eqv2_mae_dft_geom: single points at DFT-relaxed geometries vs DFT E_ads, {len(val)} held-out val "
        f"configs (CO2 and H2O), {RESEARCH['val']} (columns pred and eqv2_odac_sp). eqV2-ODAC was trained on "
        f"ODAC23 and is never used as an arm. Arm CIs: hierarchical bootstrap (pools, then runs within pools) "
        f"when an arm covers >= {MIN_POOLS_FOR_POOL_RESAMPLING} pools; otherwise conditional on the pools.")
    return {"mace_auc": mace_auc, "mace_auc_ci": mace_ci, "gas_relax_auc": gas_relax_auc,
            "dft_init_auc": dft_init_auc, "mace_mae_dft_geom": mace_mae, "eqv2_mae_dft_geom": eqv2_mae,
            "notes": notes}


def ceilings_block(data, pools):
    out = []
    for p in pools:
        df = data.pool(p)
        h = int(df.hit.sum())
        out.append({"pool": p, "oracle_hits": min(BUDGET, h),
                    "dft_init_hits": stats.top_k_hits(dac_score(df.E_CO2_init, df.E_H2O_init), df.hit),
                    "random_expected": BUDGET * h / len(df)})
    return out


def prereg_sha(runs, root):
    shas = Counter(r.header.get("prereg_sha") for r in runs if r.header.get("prereg_sha"))
    if shas:
        return shas.most_common(1)[0][0]
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "prereg-v1^{commit}"],
                             check=False, capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unfrozen"


def compute(root=ROOT, include_mock=False, status=None, pools=TEST_POOLS):
    root = Path(root)
    data = load_data(root / "data")
    base = pd.read_parquet(root / "results" / "baselines.parquet")
    hit_of = dict(zip(data.labels.id, data.labels.hit.astype(bool)))
    runs = load_runs(root / "runs", hit_of, include_mock)
    pool_hits = {p: data.pool_hits(p) for p in pools}
    pool_n = {p: len(data.pool(p)) for p in pools}
    n_total = sum(pool_n.values())

    arm_flags = {**baseline_runs(base, pools), **llm_runs(runs, pools)}
    arms = [arm_entry(a, "baseline" if a in BASELINE_ARMS else "llm", arm_flags[a], pool_hits, pool_n)
            for a in [*BASELINE_ARMS, *LLM_ARMS] if a in arm_flags]
    used_mock = any(r.mock for r in runs)
    results = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "mock" if used_mock else (status or "pilot"),
        "prereg_sha": prereg_sha(runs, root),
        "budget": BUDGET,
        "base_rate": sum(pool_hits.values()) / n_total,
        "pools": [{"pool": p, "n": pool_n[p], "hits": pool_hits[p]} for p in pools],
        "arms": arms,
        "tests": tests_block(arm_flags),
        "calibration": calibration_block(runs, pools),
        "critic": critic_block(runs, pools),
        "honesty": honesty_block(data, pools, root),
        "leak": leak_block(runs, data),
        "claims": claims_block(runs, pools),
        "ceilings": ceilings_block(data, pools),
    }
    return clean(results), clean(pools_doc(data)), runs


def pools_doc(data):
    cols = ["id", "S_mace", "E_CO2_mace", "E_H2O_mace", "pld_A", "lcd_A", "oms", "metals", "cost_tier", "hit",
            "E_CO2", "E_H2O"]
    out = {}
    for p in ALL_POOLS:
        df = data.pool(p).rename(columns={"metal_cost_tier": "cost_tier"})
        for c in cols:
            if c not in df:
                df[c] = None
        df = df[cols].astype(object).where(df[cols].notna(), None)
        rows = df.to_dict("records")
        for r in rows:
            r["oms"] = None if r["oms"] is None else bool(r["oms"])
            r["hit"] = bool(r["hit"])
            r["cost_tier"] = None if r["cost_tier"] is None else int(r["cost_tier"])
        out[p] = rows
    return {"pools": out}


def write_json(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, allow_nan=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--include-mock", action="store_true")
    ap.add_argument("--status", choices=["pilot", "final"], default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--pools", nargs="+", default=TEST_POOLS, help="analysis pools (P0 only for pilot checks)")
    a = ap.parse_args([] if argv is None else argv)
    root = Path(a.root)
    results, pools, runs = compute(root, a.include_mock, a.status, a.pools)
    write_json(results, root / "results" / "results.json")
    (root / "results" / "pools.json").write_text(json.dumps(pools, separators=(",", ":"), allow_nan=False) + "\n")
    for r in runs:
        for msg in r.mismatches:
            print(f"warning: {r.run_id}: {msg}")
    print(f"{len(runs)} LLM runs; status {results['status']}")
    for arm in results["arms"]:
        print(f"{arm['arm']} {arm['label']:28s} n={arm['n_runs']:4d} hits@40 {arm['hits_mean']:.2f} "
              f"[{arm['hits_ci'][0]:.2f}, {arm['hits_ci'][1]:.2f}]")
    for t in results["tests"]:
        print(f"{t['name']}: diff {t['diff']:+.2f} [{t['ci'][0]:+.2f}, {t['ci'][1]:+.2f}] p={t['p']:.4f}")


if __name__ == "__main__":
    main(sys.argv[1:])
