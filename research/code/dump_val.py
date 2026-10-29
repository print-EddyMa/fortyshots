"""Dump single-adsorbate ODAC23 val configs (DFT-relaxed geometry) to extxyz + eqV2-ODAC single point.
usage: python -I dump_val.py <val_dir> <out.extxyz> <n_per_gas> [ckpt]"""
import sys, os, glob, json, time, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, lmdb, torch
from peek_lmdb import load
from ase import Atoms
from ase.io import write
torch.set_num_threads(4)
root, outp, nper = sys.argv[1], sys.argv[2], int(sys.argv[3])
ck = sys.argv[4] if len(sys.argv) > 4 else None
recs = []
for f in sorted(glob.glob(root + "/*.lmdb")):
    env = lmdb.open(f, subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        for k, v in tx.cursor():
            d = load(v)
            if hasattr(d, "keys") and d.nads == 1 and abs(float(d.y_relaxed)) < 5 and int(d.natoms) <= int(os.environ.get("MAXAT", "220")): recs.append(d)
rng = np.random.default_rng(0)
sel = []
for gas, key in [("CO2", "nco2"), ("H2O", "nh2o")]:
    pool = [d for d in recs if getattr(d, key) == 1]
    sel += [pool[i] for i in rng.choice(len(pool), size=min(nper, len(pool)), replace=False)]
calc = None
if ck:
    from fairchem.core.common.relaxation.ase_utils import OCPCalculator
    calc = OCPCalculator(checkpoint_path=ck, cpu=True, seed=0)
frames = []; ts = []
for d in sel:
    a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos_relaxed.numpy(), cell=d.cell.numpy()[0], pbc=True)
    a.set_tags(d.tags.numpy().astype(int))
    a.info.update(name=d.name, y_relaxed=float(d.y_relaxed), y_init=float(d.y_init), gas="CO2" if d.nco2 else "H2O", defective=bool(d.defective))
    if calc is not None:
        b = a.copy(); b.calc = calc; t = time.time(); a.info["eqv2_odac_sp"] = float(b.get_potential_energy()); ts.append(time.time() - t); print(len(ts), len(a), round(ts[-1],1), flush=True)
    frames.append(a)
write(outp, frames, format="extxyz")
print("n", len(frames), "tags uniq", np.unique(np.concatenate([f.get_tags() for f in frames])), "eqv2 median s", np.median(ts) if ts else None)
