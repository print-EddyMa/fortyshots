"""Checks on the committed physics data products (skipped until they are built) and the pure helpers."""
import json
import sys

import numpy as np
import pandas as pd
import pytest

from bench.common import ROOT, dac_score

sys.path.insert(0, str(ROOT / "physics"))
import aggregate_mace
import descriptors

DATA = ROOT / "data"
POOLS = ["P0", "P1", "P2", "P3", "P4", "P5", "V"]
LABEL_COLS = {"E_CO2", "E_H2O", "hit", "E_CO2_init", "E_H2O_init", "y_relaxed", "y_init"}


def pool_ids():
    u = pd.read_parquet(DATA / "universe.parquet")
    return set(u[u.pool.isin(POOLS)].id)


def test_cost_tier():
    assert descriptors.cost_tier(["Zn"]) == 1
    assert descriptors.cost_tier(["Zn", "Co"]) == 2
    assert descriptors.cost_tier(["Cu", "Eu"]) == 3
    assert descriptors.cost_tier(["Cd"]) == 3
    assert descriptors.cost_tier([]) == 1


def test_aggregate_minimum_and_labels():
    place = pd.DataFrame([
        {"kind": "placement", "id": "m1", "gas": "CO2", "placement": 0, "name": "REF_w_CO2_1", "e_ads": -0.2},
        {"kind": "placement", "id": "m1", "gas": "CO2", "placement": 1, "name": "REF_w_CO2_2", "e_ads": -0.6},
        {"kind": "placement", "id": "m1", "gas": "H2O", "placement": 0, "name": "REF_w_H2O_random_1", "e_ads": -0.4},
    ])
    cell = np.eye(3) * 10.0
    pos = np.array([[9.5, 5.0, 5.0], [0.0, 5.0, 5.0], [0.16, 5.0, 5.0]])  # O split across the boundary
    frames = {"m1": {"cell": cell, "ads": {"REF_w_CO2_2": {"pos": pos, "numbers": np.array([8, 6, 8])}}}}
    out = aggregate_mace.aggregate(place, frames)
    r = out.iloc[0]
    assert r.E_CO2_mace == -0.6 and r.E_H2O_mace == -0.4
    assert r.S_mace == pytest.approx(float(dac_score(-0.6, -0.4)))
    assert r.co2_best_placement == "CO2_2" and r.h2o_best_placement == "H2O_random_1"
    assert np.allclose(json.loads(r.co2_best_ads_pos), [[0.0, 5, 5], [-0.5, 5, 5], [0.16, 5, 5]])
    assert list(out.columns) == aggregate_mace.COLUMNS


@pytest.mark.skipif(not (DATA / "descriptors.parquet").exists(), reason="descriptors not built")
def test_descriptors_complete_and_label_free():
    d = pd.read_parquet(DATA / "descriptors.parquet")
    assert list(d.columns) == descriptors.COLUMNS
    assert not LABEL_COLS & set(d.columns)
    assert set(d.id) == pool_ids() and d.id.is_unique
    assert d.metal_cost_tier.isin([1, 2, 3]).all()
    assert d.oms.dtype == bool
    ok = d.dropna(subset=["lcd_A", "pld_A"])
    assert len(ok) >= 0.99 * len(d)
    assert (ok.lcd_A >= ok.pld_A - 1e-6).all()
    assert d.void_fraction.dropna().between(0, 1).all() and (d.density_gcm3 > 0).all()


@pytest.mark.skipif(not (DATA / "mace_cache.parquet").exists(), reason="MACE cache not built")
def test_mace_cache_shape_and_no_refcodes():
    m = pd.read_parquet(DATA / "mace_cache.parquet")
    assert list(m.columns) == aggregate_mace.COLUMNS and m.id.is_unique
    assert set(m.id) <= pool_ids()
    assert np.allclose(m.S_mace, dac_score(m.E_CO2_mace, m.E_H2O_mace))
    assert m.co2_best_placement.str.startswith("CO2").all() and m.h2o_best_placement.str.startswith("H2O").all()
    refcodes = set(pd.read_parquet(DATA / "id_map.parquet").refcode)
    assert not any(p.split("_")[0] in refcodes for p in m.co2_best_placement)
    for txt in m.co2_best_ads_pos:
        pos = np.array(json.loads(txt))
        assert pos.shape == (3, 3)
        assert np.allclose(np.linalg.norm(pos[1:] - pos[0], axis=1), 1.17, atol=0.15)
