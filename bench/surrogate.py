"""Pre-registered random-forest surrogate shared by arm A3 and the agent's fit_surrogate tool.

RF regression of the DFT DAC score S on label-free descriptors plus the cheap MACE values.
Hyperparameters are fixed (sklearn defaults except n_estimators=500); NaN features are handled
natively by sklearn >= 1.4. The predictive std is the spread of the per-tree predictions.
"""
import numpy as np
from sklearn.ensemble import RandomForestRegressor

N_TREES = 500
# The 12 most common metals in the dev pool P0 (data/descriptors.parquet); fixed list, frozen with PREREG.
METAL_FLAGS = ["Zn", "Cu", "Cd", "Mn", "Co", "Ni", "Fe", "La", "Al", "Nd", "Eu", "Gd"]
NUMERIC = ["natoms", "volume_A3", "density_gcm3", "lcd_A", "pld_A", "asa_m2g", "void_fraction",
           "n_metal_atoms", "metal_cost_tier", "E_CO2_mace", "E_H2O_mace", "S_mace"]
FEATURES = [*NUMERIC, "oms", "metal_density", *(f"has_{m}" for m in METAL_FLAGS)]


def features(df):
    """Feature matrix (float, NaN allowed) in FEATURES order."""
    cols = {c: df[c].astype(float).to_numpy() if c in df else np.full(len(df), np.nan) for c in NUMERIC}
    cols["oms"] = df["oms"].astype(float).to_numpy() if "oms" in df else np.full(len(df), np.nan)
    cols["metal_density"] = cols["n_metal_atoms"] / cols["volume_A3"] * 1000.0
    metals = df["metals"].fillna("").str.split(",") if "metals" in df else [[]] * len(df)
    for m in METAL_FLAGS:
        cols[f"has_{m}"] = np.array([float(m in ms) for ms in metals])
    return np.column_stack([cols[c] for c in FEATURES])


def fit_predict(train_df, y, test_df, seed):
    """Fit on (train_df, y); return mean and per-tree std of predictions on test_df."""
    rf = RandomForestRegressor(n_estimators=N_TREES, random_state=seed, n_jobs=1)
    rf.fit(features(train_df), np.asarray(y, dtype=float))
    xt = features(test_df)
    per_tree = np.stack([t.predict(xt) for t in rf.estimators_])
    return per_tree.mean(axis=0), per_tree.std(axis=0)


def ucb_rank(mean, std, kappa=1.0):
    """Lower is better (S is minimised): acquisition = mean - kappa * std."""
    return np.asarray(mean) - kappa * np.asarray(std)
