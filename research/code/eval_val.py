import sys, json, numpy as np
from scipy.stats import spearmanr
def stats(y, p):
    e = p - y; return f"MAE={np.abs(e).mean():.3f} medAE={np.median(np.abs(e)):.3f} bias={e.mean():+.3f} rho={spearmanr(y,p).statistic:.2f} n_err>0.5={int((np.abs(e)>0.5).sum())}"
first = True
for fn in sys.argv[1:]:
    R = [json.loads(l) for l in open(fn)]
    for gas in ["CO2", "H2O", "all"]:
        s = [r for r in R if gas == "all" or r["gas"] == gas]
        y = np.array([r["y_relaxed"] for r in s]); p = np.array([r["pred"] for r in s]); q = np.array([r["eqv2_odac_sp"] for r in s])
        print(f"{fn.split('val_')[-1][:-6]:12s} {gas:4s} n={len(s)} {stats(y,p)}")
        if first: print(f"{'eqV2-ODAC':12s} {gas:4s} n={len(s)} {stats(y,q)}")
    first = False
