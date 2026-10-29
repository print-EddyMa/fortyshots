"""Builds a tiny synthetic dataset matching the CONTRACTS.md data columns (hermetic core tests).

usage: python tests/fixtures/make_fixture.py   -> writes tests/fixtures/data/
Two pools (P0, P1) of 120 MOFs each. Labels are synthetic; refcodes are fake.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "data"
N_PER_POOL = 120
METALS = {"Zn": 1, "Cu": 1, "Mg": 1, "Al": 1, "Fe": 1, "Co": 2, "Ni": 2, "Mn": 2, "Zr": 2, "Cd": 3, "In": 3, "Pd": 3}
Z = {"H": 1, "C": 6, "N": 7, "O": 8, "Mg": 12, "Al": 13, "Mn": 25, "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30,
     "Zr": 40, "Pd": 46, "Cd": 48, "In": 49}


def main():
    rng = np.random.default_rng(20261029)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "structures").mkdir(exist_ok=True)
    rows, labels, idmap, mace, desc = [], [], [], [], []
    letters = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    for k, pool in enumerate(["P0", "P1"]):
        for j in range(N_PER_POOL):
            refcode = "".join(rng.choice(letters, 6))
            mid = "m" + hashlib.sha256(f"fixture:{refcode}:{k}:{j}".encode()).hexdigest()[:7]
            metal = str(rng.choice(list(METALS)))
            n_metal = int(rng.integers(1, 5))
            n_c, n_h, n_o, n_n = (int(x) for x in rng.integers([6, 4, 4, 0], [20, 16, 10, 4]))
            natoms = n_metal + n_c + n_h + n_o + n_n
            pld, oms = float(rng.uniform(2.6, 9.0)), bool(rng.random() < 0.4)
            lcd = pld + float(rng.uniform(0.2, 3.0))
            # Synthetic DFT labels: OMS + narrow pores bind more, a few strong chemisorbers.
            strong = rng.random() < (0.12 if oms and pld < 6 else 0.04)
            e_co2 = float(rng.normal(-1.0, 0.3) if strong else rng.normal(-0.3, 0.2))
            e_h2o = float(e_co2 + rng.normal(0.3 if strong else -0.1, 0.3))
            hit = bool(e_co2 < -0.5 and e_co2 < e_h2o)
            # Cheap oracle with weak ranking signal.
            e_co2_m = float(0.15 * e_co2 + rng.normal(-0.2, 0.35))
            e_h2o_m = float(0.15 * e_h2o + rng.normal(-0.2, 0.35))
            s_mace = e_co2_m + max(0.0, e_co2_m - e_h2o_m + 0.1)
            a = float(rng.uniform(8, 16))
            cell = [[a, 0.0, 0.0], [0.0, a * float(rng.uniform(0.8, 1.2)), 0.0], [0.0, 0.0, a * float(rng.uniform(0.8, 1.2))]]
            frame_syms = [metal] * n_metal + ["C"] * n_c + ["H"] * n_h + ["O"] * n_o + ["N"] * n_n
            frac = rng.random((natoms, 3))
            pos = (frac @ np.array(cell)).round(3)
            centre = np.array(cell).sum(0) / 2 + rng.normal(0, 1.0, 3)
            axis = rng.normal(0, 1, 3)
            axis /= np.linalg.norm(axis)
            co2 = np.array([centre, centre + 1.16 * axis, centre - 1.16 * axis]).round(3)
            relaxed = np.vstack([pos, co2 + rng.normal(0, 0.2, (3, 3))]).round(3)
            struct = {"id": mid, "cell": cell, "numbers": [Z[s] for s in frame_syms] + [6, 8, 8],
                      "ads": [natoms, natoms + 1, natoms + 2], "pos": np.vstack([pos, co2]).tolist(),
                      "pos_relaxed": relaxed.tolist()}
            (OUT / "structures" / f"{mid}.json").write_text(json.dumps(struct, separators=(",", ":")))
            vol = abs(float(np.linalg.det(np.array(cell))))
            mass = n_metal * 50 + n_c * 12 + n_h + n_o * 16 + n_n * 14
            rows.append({"id": mid, "split": "train", "pool": pool, "natoms_framework": natoms,
                         "n_co2_placements": int(rng.integers(1, 6)), "n_h2o_placements": int(rng.integers(1, 6))})
            labels.append({"id": mid, "E_CO2": e_co2, "E_H2O": e_h2o, "hit": hit,
                           "E_CO2_init": e_co2 + 0.4, "E_H2O_init": e_h2o + 0.3})
            idmap.append({"id": mid, "refcode": refcode})
            mace.append({"id": mid, "E_CO2_mace": e_co2_m, "E_H2O_mace": e_h2o_m, "S_mace": s_mace,
                         "co2_best_placement": 0, "h2o_best_placement": 0,
                         "co2_best_ads_pos": json.dumps(co2.tolist())})
            formula = f"{metal}{n_metal}C{n_c}H{n_h}{f'N{n_n}' if n_n else ''}O{n_o}"
            desc.append({"id": mid, "formula": formula, "metals": metal, "n_metal_atoms": n_metal, "natoms": natoms,
                         "volume_A3": vol, "density_gcm3": mass * 1.6605 / vol, "oms": oms, "lcd_A": lcd, "pld_A": pld,
                         "asa_m2g": float(rng.uniform(200, 3500)), "void_fraction": float(rng.uniform(0.2, 0.8)),
                         "metal_cost_tier": METALS[metal]})
    pd.DataFrame(rows).to_parquet(OUT / "universe.parquet", index=False)
    pd.DataFrame(labels).to_parquet(OUT / "labels.parquet", index=False)
    pd.DataFrame(idmap).to_parquet(OUT / "id_map.parquet", index=False)
    pd.DataFrame(mace).to_parquet(OUT / "mace_cache.parquet", index=False)
    pd.DataFrame(desc).to_parquet(OUT / "descriptors.parquet", index=False)
    lab = pd.DataFrame(labels).merge(pd.DataFrame(rows), on="id")
    print(lab.groupby("pool").hit.agg(["sum", "count"]))


if __name__ == "__main__":
    main()
