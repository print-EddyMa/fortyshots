"""Framework displacement during DFT relaxation (initial->relaxed) for best CO2 config per MOF; hits vs non-hits."""
import sys, os, glob, json, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, lmdb
from peek_lmdb import load
import pandas as pd
pool = pd.read_csv(sys.argv[2], index_col=0)
best = {}
for f in sorted(glob.glob(sys.argv[1] + "/*.lmdb")):
    env = lmdb.open(f, subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        for k, v in tx.cursor():
            d = load(v)
            if not hasattr(d, "keys") or d.nads != 1 or not d.nco2 or d.defective: continue
            m = d.name.split("_w_")[0]
            if m not in pool.index or abs(float(d.y_relaxed)) > 5: continue
            if m not in best or d.y_relaxed < best[m][0]:
                fw = d.tags.numpy() == 0
                cell = d.cell.numpy()[0]; dv = (d.pos_relaxed.numpy() - d.pos.numpy())[fw]
                frac = dv @ np.linalg.inv(cell); frac -= np.round(frac); dv = frac @ cell
                disp = np.linalg.norm(dv, axis=1)
                best[m] = (float(d.y_relaxed), float(disp.max()), float(np.sqrt((disp ** 2).mean())))
df = pd.DataFrame(best, index=["y", "max_disp", "rmsd"]).T.join(pool[["hit"]])
print(df.groupby("hit")[["max_disp", "rmsd"]].describe().T.round(2).to_string())
print("frac with max framework displacement > 1 A:", df.groupby("hit").apply(lambda x: (x.max_disp > 1.0).mean()).round(2).to_dict())
