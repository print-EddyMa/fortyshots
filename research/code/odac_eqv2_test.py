"""Test Meta FAIR ODAC23 EquiformerV2 S2EF checkpoint on held-out ODAC23 val configs.
Initial-structure energy vs DFT y_init, and ML relaxation vs DFT y_relaxed.
usage: python -I odac_eqv2_test.py <ckpt> <val.lmdb> <n>"""
import sys, os, time, json, warnings, io, pickle
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, torch, lmdb
from peek_lmdb import load
from ase import Atoms
from ase.constraints import FixAtoms
from ase.optimize import BFGS
from fairchem.core.common.relaxation.ase_utils import OCPCalculator

torch.set_num_threads(int(os.environ.get("NT", "4")))
ckpt, lm, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
t = time.time()
calc = OCPCalculator(checkpoint_path=ckpt, cpu=True, seed=0)
print("load_s", round(time.time() - t, 1), flush=True)
env = lmdb.open(lm, subdir=False, readonly=True, lock=False)
rows = []
with env.begin() as tx:
    keys = [k for k, _ in tx.cursor()]
    rng = np.random.default_rng(1)
    pick = rng.choice(len(keys), size=n, replace=False)
    for i in pick:
        d = load(tx.get(keys[i]))
        if not hasattr(d, "keys"):
            continue
        a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos.numpy(), cell=d.cell.numpy()[0], pbc=True)
        a.set_tags(d.tags.numpy().astype(int))
        a.set_constraint(FixAtoms(mask=d.fixed.numpy().astype(bool)))
        a.calc = calc
        t0 = time.time(); e0 = a.get_potential_energy(); tsp = time.time() - t0
        t0 = time.time(); opt = BFGS(a, logfile=None); opt.run(fmax=0.05, steps=100); trel = time.time() - t0
        e1 = a.get_potential_energy()
        r = dict(name=d.name, natoms=int(d.natoms), nfixed=int(d.fixed.sum()), y_init=round(float(d.y_init), 3), ml_init=round(float(e0), 3),
                 y_relaxed=round(float(d.y_relaxed), 3), ml_relaxed=round(float(e1), 3), steps=opt.nsteps, t_sp=round(tsp, 2), t_relax=round(trel, 1))
        print(json.dumps(r), flush=True); rows.append(r)
e = np.array([[r["y_relaxed"], r["ml_relaxed"]] for r in rows])
print("MAE_relaxed_eV", round(float(np.abs(e[:, 0] - e[:, 1]).mean()), 3))
