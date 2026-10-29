"""Cheap oracle cache: MACE-MP-0 medium (float32, no D3) single points at ODAC23 initial placements.

E_ads = E(MOF+gas) - E(MOF) - E(gas), framework and gas at their ODAC/relaxed geometries, no relaxation.
Per MOF and gas, up to MAX_PLACEMENTS placements (sorted by config name). Resumable and sharded:
worker k of n handles MOFs whose position in the priority order is k mod n.

usage: OMP_NUM_THREADS=2 python -I physics/mace_cache.py <configs.extxyz> <data_dir> <out_dir> <k> <n>
"""
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from ase.build import molecule  # noqa: E402
from ase.io import iread  # noqa: E402
from ase.optimize import BFGS  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_data import hid  # noqa: E402

MAX_PLACEMENTS = 5
PRIORITY = ["P1", "P2", "P3", "P4", "P5", "P0", "V"]


def main():
    src, data, out, k, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))
    uni = pd.read_parquet(f"{data}/universe.parquet")
    uni = uni[uni.pool.isin(PRIORITY)].copy()
    uni["prio"] = uni.pool.map(PRIORITY.index)
    order = uni.sort_values(["prio", "id"]).id.tolist()
    mine = set(order[k::n])
    os.makedirs(out, exist_ok=True)
    path = f"{out}/shard-{k}.jsonl"
    done = set()
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            if r.get("kind") == "done":
                done.add(r["id"])

    configs = {}
    for a in iread(src, index=":"):
        i = hid(a.info["mof"])
        if i in mine and i not in done:
            configs.setdefault(i, []).append(a)

    from mace.calculators import mace_mp
    calc = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)

    def energy(atoms):
        b = atoms.copy()
        b.calc = calc
        return float(b.get_potential_energy())

    gas_e = {}
    for g in ["CO2", "H2O"]:
        m = molecule(g)
        m.set_cell([25, 25, 25])
        m.center()
        m.pbc = True
        m.calc = calc
        BFGS(m, logfile=None).run(fmax=0.01, steps=200)
        gas_e[g] = float(m.get_potential_energy())

    todo = [i for i in order if i in configs]
    print(f"worker {k}/{n}: {len(todo)} MOFs to do, {len(done)} already done", flush=True)
    with open(path, "a") as fh:
        for j, i in enumerate(todo):
            t0 = time.time()
            frames = sorted(configs[i], key=lambda a: a.info["name"])
            ads0 = frames[0].get_tags() > 0
            e_mof = energy(frames[0][~ads0])
            for gas in ["CO2", "H2O"]:
                sel = [a for a in frames if a.info["gas"] == gas][:MAX_PLACEMENTS]
                for p, a in enumerate(sel):
                    e = energy(a) - e_mof - gas_e[gas]
                    fh.write(json.dumps({"kind": "placement", "id": i, "gas": gas, "placement": p,
                                         "name": a.info["name"], "e_ads": e}) + "\n")
            fh.write(json.dumps({"kind": "done", "id": i, "natoms": len(frames[0]) - int(ads0.sum()),
                                 "sec": round(time.time() - t0, 2)}) + "\n")
            fh.flush()
            if j % 25 == 0:
                print(f"worker {k}: {j}/{len(todo)} {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
