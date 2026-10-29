"""Fast oracle: single point at each ODAC initial placement, E_int = E(complex)-E(framework)-E(gas); MOF score = min over placements.
usage: python -I fast_oracle.py <init.extxyz> <model: mace-mp0|eqv2-odac> <out.jsonl> [ckpt]"""
import sys, os, json, time, warnings; warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase.build import molecule
from ase.optimize import BFGS
torch.set_num_threads(int(os.environ.get("NT", "4")))
fr = read(sys.argv[1], ":"); which = sys.argv[2]; out = open(sys.argv[3], "w")
if which == "eqv2-odac":
    from fairchem.core.common.relaxation.ase_utils import OCPCalculator
    calc = OCPCalculator(checkpoint_path=sys.argv[4], cpu=True, seed=0); referenced = True
else:
    from mace.calculators import mace_mp
    calc = mace_mp(model="medium", device="cpu", default_dtype=os.environ.get("DT", "float32"), dispersion=False); referenced = False
def E(a):
    b = a.copy(); b.calc = calc; return float(b.get_potential_energy())
gasE = {}
if not referenced:
    for g in ["CO2", "H2O"]:
        m = molecule(g); m.set_cell([25, 25, 25]); m.center(); m.pbc = True; m.calc = calc
        BFGS(m, logfile=None).run(fmax=0.01, steps=200); gasE[g] = float(m.get_potential_energy())
frameE = {}; t0 = time.time(); n = 0
for a in fr:
    ads = a.get_tags() > 0
    if referenced:
        pred = E(a)  # ODAC-trained model predicts adsorption-referenced energy directly
    else:
        key = a.info["mof"]
        if key not in frameE: frameE[key] = E(a[~ads])
        pred = E(a) - frameE[key] - gasE[a.info["gas"]]
    n += 1
    out.write(json.dumps(dict(mof=a.info["mof"], gas=a.info["gas"], name=a.info["name"], natoms=len(a), y_relaxed=a.info["y_relaxed"], y_init=a.info["y_init"], pred=pred)) + "\n"); out.flush()
print(which, "configs", n, "wall_s", round(time.time() - t0), "s/config", round((time.time() - t0) / max(n, 1), 2))
