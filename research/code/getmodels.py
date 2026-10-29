import time, warnings; warnings.filterwarnings("ignore")
t=time.time()
from mace.calculators import mace_mp
c=mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
print("mace medium ok", time.time()-t)
t=time.time()
try:
    c=mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float32")
    print("mace mpa ok", time.time()-t)
except Exception as e: print("mpa fail", e)
t=time.time()
from orb_models.forcefield import pretrained
import inspect
print([n for n in dir(pretrained) if n.startswith("orb_v3")])
m=pretrained.orb_v3_conservative_inf_omat(device="cpu", precision="float32-high")
print("orb ok", time.time()-t)
