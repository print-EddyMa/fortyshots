"""MOF-level evaluation of a fast oracle vs ODAC23 DFT labels (held-out val MOFs).
usage: python -I eval_fast.py <fast.jsonl> [...]"""
import sys, json, numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

rng = np.random.default_rng(0)


def dac_score(co2, h2o, margin=0.1):
    # lower = better: strong CO2 binding, penalised when water out-competes CO2
    return co2 + np.maximum(0.0, co2 - h2o + margin)


for fn in sys.argv[1:]:
    R = pd.DataFrame([json.loads(l) for l in open(fn)])
    R = R[R.y_relaxed.abs() < 5]
    g = R.groupby(["mof", "gas"]).agg(pred=("pred", "min"), y=("y_relaxed", "min"), yi=("y_init", "min")).unstack()
    g = g.dropna()
    y_co2, y_h2o = g[("y", "CO2")], g[("y", "H2O")]
    p_co2, p_h2o = g[("pred", "CO2")], g[("pred", "H2O")]
    hit = ((y_co2 < -0.5) & (y_co2 < y_h2o)).values
    s = dac_score(p_co2.values, p_h2o.values)
    n = len(g); k = 10
    top = np.argsort(s)[:k]
    rec = hit[top].sum()
    boots = []
    for _ in range(2000):
        idx = rng.integers(0, n, n)
        if hit[idx].sum() == 0 or hit[idx].all(): continue
        boots.append(roc_auc_score(hit[idx], -s[idx]))
    print(f"{fn}: MOFs={n} DFT-hits={hit.sum()} ({hit.mean():.1%}) | Spearman CO2 min-E: {spearmanr(y_co2, p_co2).statistic:.2f}, H2O: {spearmanr(y_h2o, p_h2o).statistic:.2f}"
          f" | MAE CO2 {np.abs(y_co2 - p_co2).mean():.3f} H2O {np.abs(y_h2o - p_h2o).mean():.3f}"
          f" | hit AUC {roc_auc_score(hit, -s):.2f} [95% CI {np.percentile(boots, 2.5):.2f}-{np.percentile(boots, 97.5):.2f}]"
          f" | hits in top-{k}: {rec} (random expect {k * hit.mean():.2f})")
    yi_co2, yi_h2o = g[("yi", "CO2")], g[("yi", "H2O")]
    si = dac_score(yi_co2.values, yi_h2o.values)
    print(f"   DFT-unrelaxed ceiling (DFT y_init as oracle): Spearman CO2 {spearmanr(y_co2, yi_co2).statistic:.2f} H2O {spearmanr(y_h2o, yi_h2o).statistic:.2f} | hit AUC {roc_auc_score(hit, -si):.2f} | hits in top-{k}: {hit[np.argsort(si)[:k]].sum()}")
