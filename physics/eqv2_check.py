"""eqV2-ODAC cross-check: Meta FAIR's EquiformerV2 31M ODAC23 S2EF model scores given adsorbate structures.

The model's energy for a MOF+adsorbate system is already the adsorption energy (ODAC referencing), so no
empty-MOF or gas reference is needed. eqV2-ODAC was trained on ODAC23 (including the benchmark MOFs), so it
is a cross-check for prospective mutants and the honesty panel only, never a benchmark arm.
Needs the fairchem venv (venvB: fairchem-core 1.10, torch 2.4.1 CPU); ~7 GB RAM, ~3.5 s per single point.

Input JSON: a list of {id, cell[3][3], numbers[], pos[][3], ads[] (adsorbate atom indices)}, i.e. the
data/structures/<id>.json or physics/mutate.py format. Adsorbate tags follow ODAC: CO2 = 1, H2O = 2.

usage: OMP_NUM_THREADS=1 python -I physics/eqv2_check.py <in.json> <out.json> [--ckpt eqv2_31M.pt] [--relax 0]
"""
import argparse
import json
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")

DEFAULT_CKPT = os.environ.get("EQV2_CKPT", "eqv2_31M.pt")


def ads_tag(numbers):
    return 2 if sorted(numbers) == [1, 1, 8] else 1


def to_atoms(doc):
    from ase import Atoms
    from ase.constraints import FixAtoms
    numbers = np.asarray(doc["numbers"], int)
    a = Atoms(numbers=numbers, positions=np.asarray(doc["pos"], float), cell=doc["cell"], pbc=True)
    ads = np.asarray(doc.get("ads", []), int)
    tags = np.zeros(len(a), int)
    if len(ads):
        tags[ads] = ads_tag(numbers[ads].tolist())
    a.set_tags(tags)
    a.set_constraint(FixAtoms(mask=tags == 0))
    return a


def score(docs, ckpt, relax_steps=0, threads=1):
    import torch
    from fairchem.core.common.relaxation.ase_utils import OCPCalculator
    torch.set_num_threads(threads)
    calc = OCPCalculator(checkpoint_path=ckpt, cpu=True, seed=0)
    out = []
    for doc in docs:
        a = to_atoms(doc)
        a.calc = calc
        t0 = time.time()
        row = {"id": doc["id"], "eqv2_E_ads": float(a.get_potential_energy())}
        if relax_steps:
            from ase.optimize import BFGS
            opt = BFGS(a, logfile=None)
            opt.run(fmax=0.05, steps=relax_steps)
            row.update(eqv2_E_ads_relaxed=float(a.get_potential_energy()), steps=opt.nsteps)
        row["sec"] = round(time.time() - t0, 2)
        out.append(row)
        print(json.dumps(row), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--relax", type=int, default=0, help="BFGS steps on the adsorbate (framework fixed); 0 = SP")
    a = ap.parse_args()
    with open(a.inp) as fh:
        docs = json.load(fh)
    docs = docs if isinstance(docs, list) else [docs]
    rows = score(docs, a.ckpt, a.relax, int(os.environ.get("OMP_NUM_THREADS", "1")))
    with open(a.out, "w") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
