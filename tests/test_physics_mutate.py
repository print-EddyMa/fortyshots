import sys

import numpy as np
import pytest

from bench.common import ROOT

sys.path.insert(0, str(ROOT / "physics"))
import validity


def test_same_family():
    assert validity.same_family("Zn", "Mg") and validity.same_family("Al", "Sc")
    assert not validity.same_family("Zn", "Al")


def test_charge_check_skips_functionalization():
    assert validity.charge_check(None, None, {"op": "functionalize"}) == "n/a"


@pytest.fixture
def mutate():
    pytest.importorskip("ase")
    import mutate
    return mutate


def benzene_like():
    import ase
    from ase.build import molecule
    a = molecule("C6H6")
    a.set_cell([12, 12, 12])
    a.center()
    a.pbc = True
    zn = ase.Atoms("Zn", positions=[[1.0, 1.0, 1.0]])
    return a + zn


def test_functionalize_and_swap(mutate):
    a = benzene_like()
    sites = mutate.aromatic_ch_sites(a)
    assert len(sites) == 6
    b, tag = mutate.build(a, {"op": "functionalize", "group": "NH2", "site": 1})
    assert tag == "NH2s1" and len(b) == len(a) + 2 and b.get_chemical_symbols().count("N") == 1
    assert validity.clash_ratio(b) > 0.75
    c, tag = mutate.build(a, {"op": "swap_metal", "from": "Zn", "to": "Mg"})
    assert tag == "Zn2Mg" and "Mg" in c.get_chemical_symbols()
    with pytest.raises(ValueError):
        mutate.build(a, {"op": "swap_metal", "from": "Zn", "to": "Al"})
    with pytest.raises(ValueError):
        mutate.build(a, {"op": "functionalize", "group": "NO2"})


def test_subsample_and_carry_over(mutate):
    traj = [(np.eye(3), np.zeros((2, 3)) + k) for k in range(100)]
    s = mutate.subsample(traj)
    assert len(s) == 30 and s[0]["pos"][0][0] == 0 and s[-1]["pos"][0][0] == 99
    pos = mutate.carry_over([[1.0, 2.0, 3.0]], np.eye(3) * 10, np.eye(3) * 11)
    assert np.allclose(pos, [[1.1, 2.2, 3.3]])
