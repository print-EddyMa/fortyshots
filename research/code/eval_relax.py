import sys, json, pandas as pd, numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
R = pd.DataFrame([json.loads(l) for l in open(sys.argv[1])])
g = R.pivot(index="mof", columns="gas", values=["pred", "y"]).dropna()
hit = ((g[("y", "CO2")] < -0.5) & (g[("y", "CO2")] < g[("y", "H2O")])).values
s = g[("pred", "CO2")].values + np.maximum(0, g[("pred", "CO2")].values - g[("pred", "H2O")].values + 0.1)
print("MOFs", len(g), "hits", hit.sum(), "| Spearman CO2", round(spearmanr(g[("y", "CO2")], g[("pred", "CO2")]).statistic, 2), "H2O", round(spearmanr(g[("y", "H2O")], g[("pred", "H2O")]).statistic, 2),
      "| MAE CO2", round(float((g[("y", "CO2")] - g[("pred", "CO2")]).abs().mean()), 3), "| AUC(dac score)", round(roc_auc_score(hit, -s), 2) if 0 < hit.sum() < len(hit) else None,
      "| AUC(CO2 only)", round(roc_auc_score(hit, -g[("pred", "CO2")].values), 2) if 0 < hit.sum() < len(hit) else None,
      "| median s per MOF-gas", R.t.median())
k = int(hit.sum()); print(f"top-{k} by dac score contains {hit[np.argsort(s)[:k]].sum()} hits (random {k*hit.mean():.1f})")
