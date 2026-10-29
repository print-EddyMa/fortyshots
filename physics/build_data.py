"""Build the committed dataset from the private ODAC23 extract.

Writes (all keyed by hashed id, no refcodes except id_map.parquet):
  data/universe.parquet   id, split, pool, natoms_framework, n_co2_placements, n_h2o_placements
  data/labels.parquet     id, E_CO2, E_H2O, hit, E_CO2_init, E_H2O_init   (DFT, ODAC23)
  data/id_map.parquet     id, refcode   (public by design, for reproducibility)
  data/structures/<id>.json  framework + best-DFT CO2 placement, initial and relaxed positions

Universe: ODAC23 IS2R pristine MOFs with both CO2 and H2O single-adsorbate labels and
<= 200 atoms including the adsorbate. Train-split MOFs are shuffled with a fixed seed into
six disjoint pools of 500 (P0 dev, P1-P5 test). Val-split MOFs form pool V.

usage: python -I physics/build_data.py data/_build/configs.extxyz data
"""
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
from ase.io import iread

SALT = "fortyshots-v1:"
SEED = 20261029
POOL_SIZE = 500
N_POOLS = 6
HIT_CO2 = -0.5


def hid(refcode):
    return "m" + hashlib.sha256((SALT + refcode).encode()).hexdigest()[:7]


def is_hit(co2, h2o):
    return (co2 < HIT_CO2) & (co2 < h2o)


def unwrap(pos, pos_relaxed, cell):
    """Shift relaxed positions by lattice vectors to the image nearest the initial ones."""
    inv = np.linalg.inv(cell)
    d = (pos_relaxed - pos) @ inv
    return pos_relaxed - np.round(d) @ cell


def main():
    src, out = sys.argv[1], sys.argv[2]
    rows, best = [], {}
    for a in iread(src, index=":"):
        i = a.info
        rows.append({"name": i["name"], "mof": i["mof"], "gas": i["gas"], "split": i["split"], "natoms": len(a),
                     "y_init": i["y_init"], "y_relaxed": i["y_relaxed"], "oms": bool(i.get("oms", False))})
        if i["gas"] == "CO2":
            k = (i["split"], i["mof"])
            if k not in best or i["y_relaxed"] < best[k][0]:
                best[k] = (i["y_relaxed"], a)
    df = pd.DataFrame(rows)
    g = df.groupby(["split", "mof", "gas"]).agg(y=("y_relaxed", "min"), yi=("y_init", "min"),
                                                 n=("name", "size"), natoms=("natoms", "min")).unstack("gas")
    g = g.dropna(subset=[("y", "CO2"), ("y", "H2O")])
    tab = pd.DataFrame({
        "E_CO2": g[("y", "CO2")], "E_H2O": g[("y", "H2O")],
        "E_CO2_init": g[("yi", "CO2")], "E_H2O_init": g[("yi", "H2O")],
        "n_co2_placements": g[("n", "CO2")].astype(int), "n_h2o_placements": g[("n", "H2O")].astype(int),
        "natoms_framework": (g[("natoms", "CO2")] - 3).astype(int),
    }).reset_index()
    tab["hit"] = is_hit(tab.E_CO2, tab.E_H2O)
    tab["id"] = tab.mof.map(hid)
    assert tab.id.is_unique, "hash collision"

    train = tab[tab.split == "train"].sort_values("id").reset_index(drop=True)
    perm = np.random.default_rng(SEED).permutation(len(train))
    pool = np.full(len(train), "unused", dtype=object)
    for k in range(N_POOLS):
        pool[perm[k * POOL_SIZE:(k + 1) * POOL_SIZE]] = f"P{k}"
    train["pool"] = pool
    val = tab[tab.split == "val"].copy()
    val["pool"] = "V"
    tab = pd.concat([train, val], ignore_index=True)

    os.makedirs(f"{out}/structures", exist_ok=True)
    tab[["id", "split", "pool", "natoms_framework", "n_co2_placements", "n_h2o_placements"]].to_parquet(
        f"{out}/universe.parquet", index=False)
    tab[["id", "E_CO2", "E_H2O", "hit", "E_CO2_init", "E_H2O_init"]].to_parquet(f"{out}/labels.parquet", index=False)
    tab[["id", "mof"]].rename(columns={"mof": "refcode"}).to_parquet(f"{out}/id_map.parquet", index=False)

    for r in tab.itertuples():
        if r.pool == "unused":
            continue
        _, a = best[(r.split, r.mof)]
        cell = np.array(a.cell)
        pos = a.positions
        rel = unwrap(pos, a.arrays["pos_relaxed"], cell)
        doc = {"id": r.id, "cell": np.round(cell, 4).tolist(), "numbers": a.numbers.tolist(),
               "ads": np.flatnonzero(a.get_tags() > 0).tolist(),
               "pos": np.round(pos, 3).tolist(), "pos_relaxed": np.round(rel, 3).tolist()}
        with open(f"{out}/structures/{r.id}.json", "w") as fh:
            json.dump(doc, fh, separators=(",", ":"))

    s = tab.groupby("pool").agg(n=("id", "size"), hits=("hit", "sum"))
    print(s.to_string())
    print("train universe", len(train), "hits", int(train.hit.sum()))


if __name__ == "__main__":
    main()
