import sys, os, time, warnings; warnings.filterwarnings("ignore")
import numpy as np, torch
torch.set_num_threads(int(os.environ.get("NT", "4")))
from ase.io import read
cif = sys.argv[1]; which = sys.argv[2]
a = read(cif); a.pbc = True
rep = [int(x) for x in os.environ.get("REP", "1,1,1").split(",")]; a = a.repeat(rep)


def mk(w):
    if w == "mace":
        from mace.calculators import mace_mp; return mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
    if w == "mace+d3":
        from mace.calculators import mace_mp; return mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=True)
    if w == "d3torch":
        from torch_dftd.torch_dftd3_calculator import TorchDFTD3Calculator; return TorchDFTD3Calculator(device="cpu", xc="pbe", damping="bj")
    if w.startswith("orb"):
        from orb_models.forcefield import pretrained
        from orb_models.forcefield.inference.calculator import ORBCalculator
        from orb_models.forcefield.inference.d3_model import D3SumModel, AlchemiDFTD3
        m, ad = pretrained.orb_v3_conservative_inf_omat(device="cpu", precision="float32-high")
        if w == "orb+d3": m = D3SumModel(m, AlchemiDFTD3())
        return ORBCalculator(m, ad, device="cpu")


c = mk(which)
ts = []
for i in range(4):
    b = a.copy(); b.rattle(0.01, seed=i); b.calc = c
    t = time.time(); e = b.get_potential_energy(); f = b.get_forces(); ts.append(time.time() - t)
print(f"{which} natoms={len(a)} first={ts[0]:.2f}s median_rest={np.median(ts[1:]):.2f}s E={e:.2f}")
