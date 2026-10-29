"""Full relaxation (all atoms) from ODAC initial placement; E_ads vs DFT. usage: full_relax.py <extxyz> <mace|eqv2> [ckpt]"""
import sys, os, json, time, warnings; warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase.build import molecule
from ase.optimize import BFGS, FIRE
torch.set_num_threads(4)
fr = read(sys.argv[1], ":"); which = sys.argv[2]
if which == "eqv2":
    from fairchem.core.common.relaxation.ase_utils import OCPCalculator
    calc = OCPCalculator(checkpoint_path=sys.argv[3], cpu=True, seed=0)
else:
    from mace.calculators import mace_mp
    calc = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
gasE = {}
for a in fr:
    t = time.time(); b = a.copy(); b.calc = calc
    opt = BFGS(b, logfile=None); opt.run(fmax=0.05, steps=150); e = float(b.get_potential_energy())
    if which == "eqv2":
        eads = e  # adsorption-referenced
    else:
        ads = a.get_tags() > 0
        f = a[~ads].copy(); f.calc = calc; BFGS(f, logfile=None).run(fmax=0.05, steps=150)
        g = a.info["gas"]
        if g not in gasE:
            m = molecule(g); m.set_cell([25, 25, 25]); m.center(); m.pbc = True; m.calc = calc; BFGS(m, logfile=None).run(fmax=0.01, steps=200); gasE[g] = float(m.get_potential_energy())
        eads = e - float(f.get_potential_energy()) - gasE[g]
    print(json.dumps(dict(model=which, name=a.info["name"], natoms=len(a), dft_init=round(a.info["y_init"], 3), dft_relaxed=round(a.info["y_relaxed"], 3), ml_relaxed=round(eads, 3), steps=opt.nsteps, t_s=round(time.time() - t))), flush=True)
