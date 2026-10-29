import numpy as np
import pandas as pd
import pytest

from bench import arms, surrogate
from bench.common import BUDGET, ROOT, TEST_POOLS, load_data
from bench.synthetic import make_data


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = tmp_path_factory.mktemp("synth")
    make_data(root)
    return load_data(root / "data")


@pytest.mark.parametrize("arm", ["A0", "A1", "A2", "A3"])
def test_arm_buys_40_unique_pool_ids_deterministically(data, arm):
    pool = data.pool("P1")
    picks = arms.run_arm(arm, pool, 3)
    assert len(picks) == BUDGET == len(set(picks))
    assert set(picks) <= set(pool.id)
    assert arms.run_arm(arm, pool, 3) == picks


def test_a0_seeds_differ(data):
    pool = data.pool("P1")
    assert arms.run_arm("A0", pool, 0) != arms.run_arm("A0", pool, 1)


def test_a2_is_greedy_on_s_mace(data):
    pool = data.pool("P2")
    picks = arms.run_arm("A2", pool, 0)
    assert set(picks) == set(pool.nsmallest(BUDGET, "S_mace").id)


def test_a1_puts_window_oms_first(data):
    pool = data.pool("P3")
    picks = arms.run_arm("A1", pool, 0)
    window = pool[pool.oms & pool.pld_A.between(*arms.PLD_WINDOW)]
    first = pool.set_index("id").loc[picks[:min(len(window), BUDGET)]]
    assert first.oms.all() and first.pld_A.between(*arms.PLD_WINDOW).all()
    assert first.metal_cost_tier.is_monotonic_increasing


def test_a3_first_round_random_then_model(data):
    pool = data.pool("P1")
    picks = arms.run_arm("A3", pool, 0)
    rng = arms.rng_for("A3", "P1", 0)
    assert picks[:10] == list(pool.id.to_numpy()[rng.permutation(len(pool))[:10]])


def test_surrogate_features_have_no_labels(data):
    assert not {"E_CO2", "E_H2O", "hit", "S", "E_CO2_init", "E_H2O_init"} & set(surrogate.FEATURES)
    x = surrogate.features(data.pool("P1"))
    assert x.shape == (len(data.pool("P1")), len(surrogate.FEATURES))


def test_run_all_long_format(data):
    df = arms.run_all(data, ["P1"], 2, arms=("A0", "A2"), log=lambda *_: None)
    assert len(df) == 2 * 2 * BUDGET
    assert set(df["round"]) == {1, 2, 3, 4}
    assert df.groupby(["arm", "seed"]).id.nunique().eq(BUDGET).all()


BASELINES = ROOT / "results" / "baselines.parquet"


@pytest.mark.skipif(not BASELINES.exists(), reason="results/baselines.parquet not built")
def test_real_a0_matches_base_rate():
    """Per-pool Monte-Carlo SE of the 50-seed mean is ~0.14 hits, so each pool gets 3 SE and the
    five-pool average (SE ~0.06) gets the 0.15 tolerance."""
    base = pd.read_parquet(BASELINES)
    real = load_data()
    devs = []
    for pool in TEST_POOLS:
        a0 = base[(base.arm == "A0") & (base.pool == pool)]
        mean_hits = a0.groupby("seed").hit.sum().mean()
        expected = BUDGET * real.pool_hits(pool) / len(real.pool(pool))
        devs.append(mean_hits - expected)
        assert abs(mean_hits - expected) < 0.45, (pool, mean_hits, expected)
    assert abs(np.mean(devs)) < 0.15, devs


@pytest.mark.skipif(not BASELINES.exists(), reason="results/baselines.parquet not built")
def test_real_baselines_complete():
    base = pd.read_parquet(BASELINES)
    runs = base.groupby(["arm", "pool", "seed"]).size()
    assert (runs == BUDGET).all()
    assert base.groupby(["arm", "pool", "seed"]).id.nunique().eq(BUDGET).all()
    assert set(base.arm) == {"A0", "A1", "A2", "A3"}
    assert np.isin(TEST_POOLS, base.pool.unique()).all()
