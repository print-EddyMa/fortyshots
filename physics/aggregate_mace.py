"""Aggregate the MACE shards into data/mace_cache.parquet (works on partial shards).

Per MOF and gas the cheap value is the minimum E_ads over placements; S_mace is the DAC score of the
two cheap values. Placement names are stored without the refcode prefix (e.g. "CO2_random_2"), and
co2_best_ads_pos holds the ODAC initial positions of the CO2 atoms of the
best MACE placement, as a JSON string: carbon first, and the oxygens moved to their periodic image
nearest the carbon so the molecule is whole (ODAC stores about a third of the CO2s split across the cell).

usage: python -I physics/aggregate_mace.py data/_build/mace data/_build/frames.pkl data/mace_cache.parquet
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frames import load as load_frames

COLUMNS = ["id", "E_CO2_mace", "E_H2O_mace", "S_mace", "co2_best_placement", "h2o_best_placement",
           "co2_best_ads_pos"]


def dac_score(co2, h2o):
    return co2 + np.maximum(0.0, co2 - h2o + 0.1)


def whole_co2(ads, cell):
    pos, numbers = np.asarray(ads["pos"], float), list(ads["numbers"])
    c = numbers.index(6)
    o = np.delete(pos, c, axis=0)
    frac = (o - pos[c]) @ np.linalg.inv(cell)
    o = pos[c] + (frac - np.round(frac)) @ cell
    return np.vstack([pos[c], o])


def placement_label(name):
    return name.split("_w_", 1)[1]


def read_shards(shard_dir):
    place, done = [], set()
    for path in sorted(glob.glob(f"{shard_dir}/shard-*.jsonl")):
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:  # a worker may be mid-write
                    continue
                if r["kind"] == "done":
                    done.add(r["id"])
                elif r["kind"] == "placement":
                    place.append(r)
    df = pd.DataFrame(place, columns=["kind", "id", "gas", "placement", "name", "e_ads"])
    return df[df.id.isin(done)].drop_duplicates(["id", "name"], keep="last")


def aggregate(place, frames):
    best = place.loc[place.groupby(["id", "gas"]).e_ads.idxmin()]
    e = best.pivot(index="id", columns="gas", values="e_ads").dropna()
    names = best.pivot(index="id", columns="gas", values="name").loc[e.index]
    out = pd.DataFrame({"id": e.index, "E_CO2_mace": e.CO2.values, "E_H2O_mace": e.H2O.values})
    out["S_mace"] = dac_score(out.E_CO2_mace.values, out.E_H2O_mace.values)
    out["co2_best_placement"] = [placement_label(n) for n in names.CO2]
    out["h2o_best_placement"] = [placement_label(n) for n in names.H2O]
    out["co2_best_ads_pos"] = [json.dumps(np.round(whole_co2(frames[i]["ads"][n], frames[i]["cell"]), 4).tolist())
                               for i, n in zip(out.id, names.CO2)]
    return out[COLUMNS].sort_values("id").reset_index(drop=True)


def main():
    shard_dir, frames_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    df = aggregate(read_shards(shard_dir), load_frames(frames_path))
    df.to_parquet(out, index=False)
    uni = pd.read_parquet(os.path.join(os.path.dirname(out), "universe.parquet"))
    cov = uni[uni.pool != "unused"].assign(have=lambda u: u.id.isin(df.id)).groupby("pool").have.mean()
    print(f"{len(df)} MOFs -> {out}")
    print("coverage by pool:", cov.round(3).to_dict())


if __name__ == "__main__":
    main()
