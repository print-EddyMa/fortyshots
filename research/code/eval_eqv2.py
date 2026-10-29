import sys, numpy as np
from ase.io import read
from scipy.stats import spearmanr
fr = read(sys.argv[1], ":")
for gas in ["CO2", "H2O", "all"]:
    s = [f for f in fr if gas == "all" or f.info["gas"] == gas]
    y = np.array([f.info["y_relaxed"] for f in s]); p = np.array([f.info["eqv2_odac_sp"] for f in s])
    print(gas, "n", len(s), "MAE %.3f" % np.abs(y - p).mean(), "bias %.3f" % (p - y).mean(), "spearman %.2f" % spearmanr(y, p).statistic, "y range %.2f..%.2f" % (y.min(), y.max()))
