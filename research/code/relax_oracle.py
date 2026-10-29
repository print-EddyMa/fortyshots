"""Relaxation oracle: from ODAC initial placements, relax adsorbate only (framework frozen) with MACE-MP-0; MOF score = min E_int.
usage: python -I relax_oracle.py <init.extxyz> <mofs.txt> <n_place_per_gas> <steps> <out.jsonl>"""
import sys, os, json, time, warnings; warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase.build import molecule
from ase.optimize import BFGS, FIRE
from ase.constraints import FixAtoms
torch.set_num_threads(int(os.environ.get("NT", "4")))
fr = read(sys.argv[1], ":"); keep = open(sys.argv[2]).read().split(); nper = int(sys.argv[3]); steps = int(sys.argv[4]); out = open(sys.argv[5], "w")
from mace.calculators import mace_mp
calc = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
gasE = {}
for g in ["CO2", "H2O"]:
    m = molecule(g); m.set_cell([25, 25, 25]); m.center(); m.pbc = True; m.calc = calc
    BFGS(m, logfile=None).run(fmax=0.01, steps=200); gasE[g] = float(m.get_potential_energy())
by = {}
for a in fr:
    if a.info["mof"] in keep: by.setdefault((a.info["mof"], a.info["gas"]), []).append(a)
rng = np.random.default_rng(0); frameE = {}
for (mof, gas), lst in by.items():
    t = time.time()
    pick = [lst[i] for i in rng.choice(len(lst), size=min(nper, len(lst)), replace=False)]
    best = 1e9; nsteps = 0
    for a in pick:
        ads = a.get_tags() > 0
        if mof not in frameE:
            f = a[~ads].copy(); f.calc = calc; frameE[mof] = float(f.get_potential_energy())
        b = a.copy(); b.set_constraint(FixAtoms(mask=~ads)); b.calc = calc
        opt = FIRE(b, logfile=None); opt.run(fmax=0.05, steps=steps); nsteps += opt.nsteps
        best = min(best, float(b.get_potential_energy()) - frameE[mof] - gasE[gas])
    out.write(json.dumps(dict(mof=mof, gas=gas, pred=best, y=min(x.info["y_relaxed"] for x in lst), steps=nsteps, t=round(time.time() - t, 1))) + "\n"); out.flush()
