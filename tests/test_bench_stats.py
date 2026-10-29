import numpy as np
import pytest

from bench import stats


def test_curve_and_first_hit():
    flags = [False, True, False, True]
    c = stats.curve(flags)
    assert len(c) == 41 and c[0] == 0 and c[2] == 1 and c[-1] == 2
    assert stats.first_hit(flags) == 2
    assert stats.first_hit([False] * 40) == 41
    assert stats.curve([True] * 50)[-1] == 40


def test_pool_mean_weights_pools_equally():
    assert stats.pool_mean({"P1": np.array([0, 0, 0, 0.0]), "P2": np.array([4.0])}) == 2.0


def test_hier_boot_deterministic_and_covers_mean():
    rng = np.random.default_rng(0)
    groups = {f"P{k}": rng.poisson(3 + k, 50).astype(float) for k in range(1, 6)}
    a = stats.hier_boot(groups, 7, n_boot=2000)
    b = stats.hier_boot(groups, 7, n_boot=2000)
    assert np.array_equal(a, b)
    lo, hi = stats.ci(a)
    assert lo < stats.pool_mean(groups) < hi
    fixed = stats.hier_boot(groups, 7, n_boot=2000, resample_pools=False)
    assert np.std(fixed) < np.std(a)


def test_hier_boot_curves_shape():
    groups = {"P1": np.ones((5, 41)), "P2": np.zeros((3, 41))}
    draws = stats.hier_boot(groups, 1, n_boot=100)
    assert draws.shape == (100, 41)


def test_perm_test_null_and_alternative():
    rng = np.random.default_rng(1)
    same_a = {p: rng.poisson(3, 5).astype(float) for p in ("P1", "P2", "P3")}
    same_b = {p: rng.poisson(3, 50).astype(float) for p in ("P1", "P2", "P3")}
    _, p_null = stats.perm_test(same_a, same_b, 3, n_perm=2000)
    assert p_null > 0.01
    better = {p: v + 4 for p, v in same_a.items()}
    diff, p_alt = stats.perm_test(better, same_b, 3, n_perm=2000)
    assert diff > 3 and p_alt < 0.01
    worse = {p: v - 4 for p, v in same_a.items()}
    assert stats.perm_test(worse, same_b, 3, n_perm=2000)[1] > 0.9


def test_perm_test_pvalue_roughly_uniform_under_null():
    rng = np.random.default_rng(2)
    ps = []
    for k in range(200):
        a = {p: rng.poisson(2, 5).astype(float) for p in ("P1", "P2")}
        b = {p: rng.poisson(2, 20).astype(float) for p in ("P1", "P2")}
        ps.append(stats.perm_test(a, b, k, n_perm=500)[1])
    assert np.mean(np.array(ps) < 0.05) <= 0.1


def test_diff_bootstrap():
    a = {"P1": np.full(5, 4.0), "P2": np.full(5, 6.0)}
    b = {"P1": np.full(50, 1.0), "P2": np.full(50, 1.0)}
    draws = stats.hier_boot_diff(a, b, 0, n_boot=500)
    assert np.allclose(np.unique(np.round(draws, 6)), [3.0, 4.0, 5.0])


def test_brier_reliability_auc():
    p = np.array([0.05, 0.05, 0.95, 0.95])
    y = np.array([0, 0, 1, 0])
    assert stats.brier(p, y) == pytest.approx((0.05**2 * 2 + 0.05**2 + 0.95**2) / 4)
    bins = stats.reliability(p, y)
    assert [b["n"] for b in bins] == [2, 2] and bins[1]["hit_rate"] == 0.5
    assert stats.reliability([1.0], [1])[0]["n"] == 1
    assert stats.auc([-1, -2, 0, 1], [True, True, False, False]) == 1.0
    lo, hi = stats.auc_boot_ci(np.r_[np.zeros(20), np.ones(20)], np.r_[np.ones(20), np.zeros(20)].astype(bool), 0, 200)
    assert lo == hi == 1.0
    assert stats.top_k_hits([3, 1, 2, np.nan], [True, False, True, True], k=2) == 1
