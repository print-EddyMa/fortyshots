"""Benchmark statistics. Every Monte-Carlo routine takes an explicit seed, so results are exact on re-run.

Runs are summarised as hit-flag sequences in purchase order. Pools are weighted equally: an arm's
mean is the mean over pools of the per-pool mean over seeds.
"""
import numpy as np
from sklearn.metrics import roc_auc_score

from bench.common import BUDGET

N_BOOT = 10_000
N_PERM = 10_000


def curve(flags, budget=BUDGET):
    """Cumulative hits after k = 0..budget lookups; runs shorter than budget stay flat."""
    f = np.zeros(budget, dtype=float)
    f[:min(len(flags), budget)] = np.asarray(flags, dtype=float)[:budget]
    return np.concatenate([[0.0], np.cumsum(f)])


def first_hit(flags, budget=BUDGET):
    """Lookups until the first hit (1-based); budget + 1 when censored (no hit)."""
    idx = np.flatnonzero(np.asarray(flags, dtype=bool)[:budget])
    return int(idx[0]) + 1 if len(idx) else budget + 1


def pool_mean(groups):
    """groups: {pool: array (n_runs, ...)} -> equal-weight mean over pools of the per-pool means."""
    return np.mean([np.asarray(v, dtype=float).mean(axis=0) for v in groups.values()], axis=0)


def _resample_means(vals, slots, rng):
    """For each draw d, the mean of a with-replacement resample of vals[slots[d]] (runs within pool)."""
    out = np.empty((len(slots), *vals[0].shape[1:]))
    for p, v in enumerate(vals):
        sel = np.flatnonzero(slots == p)
        if len(sel):
            counts = rng.multinomial(len(v), np.full(len(v), 1.0 / len(v)), size=len(sel))
            out[sel] = (counts @ v.reshape(len(v), -1)).reshape(len(sel), *v.shape[1:]) / len(v)
    return out


def hier_boot(groups, seed, n_boot=N_BOOT, resample_pools=True):
    """Hierarchical bootstrap draws of the pool-weighted mean: resample pools, then runs within pools.

    With resample_pools=False the pools are held fixed (CI conditional on the pools)."""
    rng = np.random.default_rng(seed)
    vals = [np.asarray(v, dtype=float) for v in groups.values()]
    n_pools = len(vals)
    slots = rng.integers(0, n_pools, size=(n_boot, n_pools)) if resample_pools else \
        np.tile(np.arange(n_pools), (n_boot, 1))
    return np.mean([_resample_means(vals, slots[:, j], rng) for j in range(n_pools)], axis=0)


def hier_boot_diff(groups_a, groups_b, seed, n_boot=N_BOOT, resample_pools=True):
    """Bootstrap draws of mean(A) - mean(B) over the pools both arms share; pools resampled jointly."""
    pools = [p for p in groups_a if p in groups_b]
    rng = np.random.default_rng(seed)
    va = [np.asarray(groups_a[p], dtype=float) for p in pools]
    vb = [np.asarray(groups_b[p], dtype=float) for p in pools]
    n = len(pools)
    slots = rng.integers(0, n, size=(n_boot, n)) if resample_pools else np.tile(np.arange(n), (n_boot, 1))
    diffs = [_resample_means(va, slots[:, j], rng) - _resample_means(vb, slots[:, j], rng) for j in range(n)]
    return np.mean(diffs, axis=0)


def ci(draws, level=0.95):
    lo, hi = np.percentile(draws, [50 * (1 - level), 50 * (1 + level)], axis=0)
    return lo, hi


def perm_test(groups_a, groups_b, seed, n_perm=N_PERM):
    """One-sided pool-stratified permutation test of H1: mean(A) > mean(B).

    Statistic: mean over shared pools of (mean A - mean B). Arm labels are shuffled within each pool,
    keeping per-pool run counts. p = (1 + #{T_perm >= T_obs}) / (1 + n_perm)."""
    pools = [p for p in groups_a if p in groups_b]
    rng = np.random.default_rng(seed)
    obs, perm = 0.0, np.zeros(n_perm)
    for p in pools:
        a, b = np.asarray(groups_a[p], dtype=float), np.asarray(groups_b[p], dtype=float)
        obs += a.mean() - b.mean()
        both = np.concatenate([a, b])
        shuffled = rng.permuted(np.tile(both, (n_perm, 1)), axis=1)
        perm += shuffled[:, :len(a)].mean(axis=1) - shuffled[:, len(a):].mean(axis=1)
    obs, perm = obs / len(pools), perm / len(pools)
    return float(obs), float((1 + np.sum(perm >= obs - 1e-12)) / (1 + n_perm))


def brier(p, y):
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def reliability(p, y, n_bins=10):
    """Equal-width bins on [0, 1]; only non-empty bins are returned."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    b = np.minimum((p * n_bins).astype(int), n_bins - 1)
    return [{"p_mean": float(p[b == k].mean()), "hit_rate": float(y[b == k].mean()), "n": int((b == k).sum())}
            for k in range(n_bins) if (b == k).any()]


def auc(score_lower_better, hit):
    hit = np.asarray(hit, dtype=bool)
    s = np.asarray(score_lower_better, dtype=float)
    return float(roc_auc_score(hit, -s))


def auc_boot_ci(score_lower_better, hit, seed, n_boot=2000):
    """Percentile CI of the hit AUC over MOF-level bootstrap resamples (resamples with one class skipped)."""
    rng = np.random.default_rng(seed)
    s, h = np.asarray(score_lower_better, dtype=float), np.asarray(hit, dtype=bool)
    out = []
    for _ in range(n_boot):
        i = rng.integers(0, len(s), len(s))
        if 0 < h[i].sum() < len(i):
            out.append(roc_auc_score(h[i], -s[i]))
    return [float(x) for x in np.percentile(out, [2.5, 97.5])]


def top_k_hits(score_lower_better, hit, k=BUDGET):
    s = np.asarray(score_lower_better, dtype=float)
    order = np.argsort(np.nan_to_num(s, nan=np.inf), kind="stable")
    return int(np.asarray(hit, dtype=bool)[order[:k]].sum())
