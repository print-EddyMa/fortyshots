"""Chemical-space atlas of the six pools -> results/atlas.json (read by the web Atlas section).

A 2-D t-SNE embedding of every MOF in pools P0-P5, built only from what the agent can see (descriptors and the
cheap MACE values, never a DFT label), plus the purchase order of seed-0 baseline campaigns so the site can draw
them as paths. Labels are attached afterwards for colouring only; they do not enter the embedding.

usage: python -m bench.atlas [--root .] [--perplexity 40] [--seed 0]
"""
import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.manifold import TSNE

from bench.common import ALL_POOLS, ROOT, load_data

NUMERIC = [
    "lcd_A", "pld_A", "asa_m2g", "void_fraction", "density_gcm3", "metal_density", "oms", "metal_cost_tier",
    "E_CO2_mace", "E_H2O_mace", "S_mace",
]
N_METALS = 14  # one-hot for the most common metals; the rest share "other"
METAL_WEIGHT = 0.6  # metal identity counts a little less than the physical descriptors
PATH_ARMS = {"A0": "Random", "A2": "MACE greedy", "A3": "RF-UCB"}


def features(df: pd.DataFrame) -> tuple[np.ndarray, list[str], list[str]]:
    """Robust-scaled numeric descriptors + MACE, plus a down-weighted one-hot of the metals present."""
    d = df.copy()
    d["metal_density"] = d.n_metal_atoms / d.volume_A3 * 1000.0
    d["oms"] = d.oms.astype(float)
    cols = []
    for c in NUMERIC:
        x = d[c].astype(float).to_numpy()
        med = np.nanmedian(x)
        x = np.where(np.isfinite(x), x, med)
        q1, q3 = np.percentile(x, [25, 75])
        iqr = (q3 - q1) or (np.std(x) or 1.0)
        cols.append(np.clip((x - med) / iqr, -4, 4))
    metals = d.metals.fillna("").str.split(",")
    counts = pd.Series([m for ms in metals for m in ms if m]).value_counts()
    top = list(counts.index[:N_METALS])
    for m in top:
        cols.append(METAL_WEIGHT * 2 * metals.apply(lambda ms, m=m: float(m in ms)).to_numpy())
    cols.append(METAL_WEIGHT * 2 * metals.apply(lambda ms: float(not any(m in top for m in ms))).to_numpy())
    return np.column_stack(cols), NUMERIC, top


def embed(X: np.ndarray, perplexity: float, seed: int) -> np.ndarray:
    Y = TSNE(n_components=2, perplexity=perplexity, init="pca", learning_rate="auto", random_state=seed,
             max_iter=1500).fit_transform(X)
    Y -= Y.mean(0)
    # rotate so the long axis is horizontal (a wide map fits a wide page)
    _, _, vt = np.linalg.svd(Y, full_matrices=False)
    Y = Y @ vt.T
    lo, hi = Y.min(0), Y.max(0)
    return (Y - lo) / (hi - lo).max()  # [0, 1] on the long axis, aspect kept


def islands(df: pd.DataFrame, Y: np.ndarray, top: list[str], min_n: int = 40, max_spread: float = 0.12) -> list[dict]:
    """Where each common metal sits on the map: the median position of its single-metal MOFs, kept only when
    they form a cluster (median distance to that point at most `max_spread` of the map width)."""
    out = []
    metals = df.metals.fillna("").str.split(",")
    for m in top:
        mask = metals.apply(lambda ms, m=m: ms == [m]).to_numpy()  # single-metal MOFs only
        if mask.sum() < min_n:
            continue
        P = Y[mask]
        c = np.median(P, axis=0)
        spread = float(np.median(np.linalg.norm(P - c, axis=1)))
        if spread > max_spread:
            continue
        out.append({"metal": m, "n": int(mask.sum()), "x": round(float(c[0]), 4), "y": round(float(c[1]), 4),
                    "spread": round(spread, 4)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--perplexity", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    data = load_data(a.root / "data")
    df = pd.concat([data.pool(p) for p in ALL_POOLS], ignore_index=True)
    X, numeric, top = features(df)
    Y = embed(X, a.perplexity, a.seed)

    base = pd.read_parquet(a.root / "results" / "baselines.parquet")
    paths = {}
    for p in ALL_POOLS:
        paths[p] = {}
        for arm in PATH_ARMS:
            s = base[(base.arm == arm) & (base.pool == p) & (base.seed == 0)].sort_values("order")
            if len(s):
                paths[p][arm] = s.id.tolist()

    out = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": f"t-SNE (scikit-learn, perplexity {a.perplexity:g}, PCA init, random_state {a.seed}) on "
                  f"{len(numeric)} label-free descriptors and MACE values (robust-scaled, clipped at 4 IQR) plus a "
                  f"one-hot of the {len(top)} most common metals at weight {METAL_WEIGHT}. DFT labels are not inputs.",
        "features": numeric + [f"metal:{m}" for m in top] + ["metal:other"],
        "n": len(df),
        "points": {
            "id": df.id.tolist(),
            "pool": df.pool.tolist(),
            "x": [round(float(v), 4) for v in Y[:, 0]],
            "y": [round(float(v), 4) for v in Y[:, 1]],
            "hit": [bool(h) for h in df.hit],
            "metals": df.metals.fillna("").tolist(),
            "E_CO2": [round(float(v), 3) for v in df.E_CO2],
            "E_H2O": [round(float(v), 3) for v in df.E_H2O],
            "pld": [round(float(v), 2) for v in df.pld_A],
        },
        "islands": islands(df, Y, top),
        "paths": {"arms": PATH_ARMS, "seed": 0, "by_pool": paths},
    }
    dest = a.root / "results" / "atlas.json"
    dest.write_text(json.dumps(out, separators=(",", ":")))
    print(f"atlas: {len(df)} MOFs, {int(df.hit.sum())} hits -> {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
