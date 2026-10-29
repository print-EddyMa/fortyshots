"""Dump ODAC23 initial placements (single adsorbate, pristine, natoms<=MAXAT) for a split to extxyz.
usage: python -I dump_init.py <split_dir> <out.extxyz>"""
import sys, os, glob, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lmdb
from peek_lmdb import load
from ase import Atoms
from ase.io import write
keep = set(open(os.environ["KEEP"]).read().split()) if os.environ.get("KEEP") else None
fr = []
for f in sorted(glob.glob(sys.argv[1] + "/*.lmdb")):
    env = lmdb.open(f, subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        for k, v in tx.cursor():
            d = load(v)
            if not hasattr(d, "keys") or d.nads != 1 or d.defective or int(d.natoms) > int(os.environ.get("MAXAT", "200")): continue
            if keep is not None and d.name.split("_w_")[0] not in keep: continue
            a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos.numpy(), cell=d.cell.numpy()[0], pbc=True)
            a.set_tags(d.tags.numpy().astype(int))
            a.info.update(name=d.name, mof=d.name.split("_w_")[0], gas="CO2" if d.nco2 else "H2O", y_relaxed=float(d.y_relaxed), y_init=float(d.y_init))
            fr.append(a)
write(sys.argv[2], fr, format="extxyz"); print("configs", len(fr), "mofs", len({a.info["mof"] for a in fr}))
