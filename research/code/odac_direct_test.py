"""ODAC23 IS2RE-direct EquiformerV2 (Meta FAIR, ungated): predict relaxed adsorption energy from initial structure, 1 forward pass.
usage: python -I odac_direct_test.py <ckpt> <lmdb_dir> <n>"""
import sys, os, time, json, glob, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, torch, lmdb
from peek_lmdb import load
from ase import Atoms
from fairchem.core.common.relaxation.ase_utils import OCPCalculator
torch.set_num_threads(int(os.environ.get("NT", "4")))
ckpt, root, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
calc = OCPCalculator(checkpoint_path=ckpt, cpu=True, seed=0)
print("implemented", calc.implemented_properties, flush=True)
recs = []
for f in sorted(glob.glob(root + "/*.lmdb")):
    env = lmdb.open(f, subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        for k, v in tx.cursor():
            d = load(v)
            if hasattr(d, "keys"): recs.append(d)
rng = np.random.default_rng(0)
idx = rng.choice(len(recs), size=min(n, len(recs)), replace=False)
out = []; ts = []
for i in idx:
    d = recs[i]
    a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos.numpy(), cell=d.cell.numpy()[0], pbc=True)
    a.set_tags(d.tags.numpy().astype(int)); a.calc = calc
    t = time.time(); e = float(a.get_potential_energy()); ts.append(time.time() - t)
    out.append((d.name, int(d.natoms), float(d.y_relaxed), e))
y = np.array([o[2] for o in out]); p = np.array([o[3] for o in out])
ok = np.abs(y) < 5
print(json.dumps({"n": int(ok.sum()), "MAE": round(float(np.abs(y[ok] - p[ok]).mean()), 3), "median_AE": round(float(np.median(np.abs(y[ok] - p[ok]))), 3),
                  "spearman": round(float(np.corrcoef(np.argsort(np.argsort(y[ok])), np.argsort(np.argsort(p[ok])))[0, 1]), 3),
                  "t_median_s": round(float(np.median(ts[1:])), 2), "natoms_median": int(np.median([o[1] for o in out]))}))
for o in out[:5]: print(o)
