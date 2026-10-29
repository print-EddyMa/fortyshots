import sys, numpy as np
from ase.io import read
fr = read(sys.argv[1], ":")
for f in fr[:3] + fr[40:42]:
    t = f.get_tags(); print(f.info["name"], f.info["gas"], {int(k): int(v) for k, v in zip(*np.unique(t, return_counts=True))}, f.get_chemical_symbols()[-4:])
