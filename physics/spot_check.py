"""Re-compute MACE E_ads from scratch for a few cached MOFs and compare with the shards (acceptance: 1e-4 eV).

Mirrors physics/mace_cache.py exactly (float32 MACE-MP-0 medium, no D3, framework from the first config in
name order, <= 5 placements per gas in name order, BFGS-relaxed gas references).

usage: OMP_NUM_THREADS=1 python -I physics/spot_check.py data/_build/configs.extxyz data/_build/mace [n=5] [seed=0]
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aggregate_mace import read_shards

MAX_PLACEMENTS = 5


def main():
    src, shard_dir = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    import torch
    from ase.build import molecule
    from ase.io import iread
    from ase.optimize import BFGS
    from build_data import hid
    from mace.calculators import mace_mp
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "1")))
    cached = read_shards(shard_dir)
    ids = sorted(cached.id.unique())
    pick = set(np.random.default_rng(seed).choice(ids, size=min(n, len(ids)), replace=False))
    configs = {}
    for a in iread(src, index=":"):
        i = hid(a.info["mof"])
        if i in pick:
            configs.setdefault(i, []).append(a)
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
    worst = 0.0
    for i in sorted(pick):
        frames = sorted(configs[i], key=lambda a: a.info["name"])
        ads0 = frames[0].get_tags() > 0
        e_mof = energy(frames[0][~ads0])
        for gas in ["CO2", "H2O"]:
            for a in [f for f in frames if f.info["gas"] == gas][:MAX_PLACEMENTS]:
                e = energy(a) - e_mof - gas_e[gas]
                ref = cached[(cached.id == i) & (cached.name == a.info["name"])].e_ads.iloc[0]
                worst = max(worst, abs(e - ref))
                print(f"{i} {gas} {a.info['name'].split('_w_')[1]:14s} recomputed {e:+.6f} cached {ref:+.6f} "
                      f"diff {e - ref:+.2e}", flush=True)
    print(f"max |diff| = {worst:.2e} eV over {len(pick)} MOFs -> {'PASS' if worst <= 1e-4 else 'FAIL'}")
    return 0 if worst <= 1e-4 else 1


if __name__ == "__main__":
    sys.exit(main())
