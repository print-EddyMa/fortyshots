"""Universal MLIP interaction energy at DFT-relaxed ODAC23 val geometries vs DFT adsorption energy.
E_int = E(complex) - E(framework, same geom) - E(gas, relaxed in box).
usage: python -I val_umlip.py <val_single.extxyz> <model> <out.jsonl>"""
import sys, os, time, json, warnings, resource; warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase.build import molecule
from ase.optimize import BFGS
torch.set_num_threads(int(os.environ.get("NT", "4")))
frames = read(sys.argv[1], ":"); which = sys.argv[2]; outp = sys.argv[3]


def mk(w):
    if w == "mace-mp0":
        from mace.calculators import mace_mp; return mace_mp(model="medium", device="cpu", default_dtype="float64", dispersion=False)
    if w.startswith("mace-dac"):
        from mace.calculators import MACECalculator
        from ase.calculators.mixing import SumCalculator
        c = MACECalculator(model_paths=os.environ["MACEDAC"], device="cpu", default_dtype="float64")
        if w.endswith("+d3"):
            from torch_dftd.torch_dftd3_calculator import TorchDFTD3Calculator
            return SumCalculator([c, TorchDFTD3Calculator(device="cpu", xc="pbe", damping="bj", cutoff=float(os.environ.get("D3CUT", "12.0")))])
        return c
    if w == "mace-mp0+d3":
        from mace.calculators import mace_mp; return mace_mp(model="medium", device="cpu", default_dtype="float64", dispersion=True, dispersion_cutoff=float(os.environ.get("D3CUT", "12.0")))
    if w == "mace-mpa":
        from mace.calculators import mace_mp; return mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64", dispersion=False)
    from orb_models.forcefield import pretrained
    from orb_models.forcefield.inference.calculator import ORBCalculator
    from orb_models.forcefield.inference.d3_model import D3SumModel, AlchemiDFTD3
    fn = {"orb-omat": pretrained.orb_v3_conservative_inf_omat, "orb-mpa": pretrained.orb_v3_conservative_inf_mpa}[w.replace("+d3", "")]
    m, ad = fn(device="cpu", precision="float32-highest")
    if w.endswith("+d3"): m = D3SumModel(m, AlchemiDFTD3())
    return ORBCalculator(m, ad, device="cpu")


calc = mk(which)


def E(at):
    b = at.copy(); b.calc = calc; return float(b.get_potential_energy())


gasE = {}
for g in ["CO2", "H2O"]:
    m = molecule(g); m.set_cell([25, 25, 25]); m.center(); m.pbc = True; m.calc = calc
    BFGS(m, logfile=None).run(fmax=0.01, steps=200); gasE[g] = float(m.get_potential_energy())
f = open(outp, "w"); ts = []
for a in frames:
    t = time.time()
    tags = a.get_tags(); ads = tags > 0
    if ads.sum() == 0 or ads.sum() == len(a):
        continue
    ec = E(a); ef = E(a[~ads])
    eint = ec - ef - gasE[a.info["gas"]]
    ts.append(time.time() - t)
    r = dict(name=a.info["name"], gas=a.info["gas"], natoms=len(a), y_relaxed=a.info["y_relaxed"], eqv2_odac_sp=a.info.get("eqv2_odac_sp"), pred=round(eint, 4), t=round(ts[-1], 2))
    f.write(json.dumps(r) + "\n"); f.flush()
print(which, "n", len(ts), "median s/config", round(float(np.median(ts)), 2), "maxRSS_GB", round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2))
