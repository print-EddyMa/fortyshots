import sys, os, glob, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lmdb
from peek_lmdb import load
from ase import Atoms
from ase.io import write
keep = set(open(sys.argv[2]).read().split()); fr = []
for f in sorted(glob.glob(sys.argv[1] + "/*.lmdb")):
    env = lmdb.open(f, subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        for k, v in tx.cursor():
            d = load(v)
            if hasattr(d, "keys") and d.name in keep:
                a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos.numpy(), cell=d.cell.numpy()[0], pbc=True)
                a.set_tags(d.tags.numpy().astype(int)); a.info.update(name=d.name, y_relaxed=float(d.y_relaxed), y_init=float(d.y_init), gas="CO2" if d.nco2 else "H2O"); fr.append(a)
write(sys.argv[3], fr, format="extxyz"); print(len(fr))
