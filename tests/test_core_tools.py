import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import FIXTURE_DATA

from fortyshots import tools as T
from fortyshots.tools import ARM_TOOLS, PoolEnv, tool_schemas


@pytest.fixture
def env(tmp_path):
    return PoolEnv(FIXTURE_DATA, "P0", tmp_path / "img", tools=ARM_TOOLS["A5"])


def test_request_dft_refuses_the_41st_lookup(env):
    ids = env.ids
    for k in range(4):
        out = env.request_dft(ids=ids[10 * k:10 * k + 10], rationale="r")
        assert out.ok and out.result["spent"] == 10 * (k + 1)
    out = env.call("request_dft", {"ids": [ids[40]], "rationale": "one more"}, "c0099")
    assert not out.ok and "refused" in out.summary
    assert env.spent == 40 and ids[40] not in env.bought


def test_request_dft_refuses_an_order_that_would_overrun(env):
    env.request_dft(ids=env.ids[:35], rationale="r")
    out = env.call("request_dft", {"ids": env.ids[35:41], "rationale": "r"}, "c0001")
    assert not out.ok and env.spent == 35


def test_request_dft_dedups_and_never_exposes_init_energies(env):
    a = env.request_dft(ids=env.ids[:3], rationale="r")
    b = env.request_dft(ids=env.ids[1:5], rationale="r")
    assert b.result["already_bought_not_charged"] == env.ids[1:3] and env.spent == 5
    text = json.dumps([a.result, b.result])
    assert "init" not in text and set(a.result["results"][0]) == {"id", "E_CO2", "E_H2O", "S", "hit"}
    bad = env.call("request_dft", {"ids": ["mzzzzzzz"], "rationale": "r"}, "c0001")
    assert not bad.ok


def test_labels_are_read_only_inside_request_dft(env, monkeypatch):
    seen = []
    orig = pd.read_parquet

    def spy(path, *a, **k):
        seen.append(Path(path).name)
        return orig(path, *a, **k)
    monkeypatch.setattr(T.pd, "read_parquet", spy)
    env.request_dft(ids=env.ids[:6], rationale="r")
    assert seen == ["labels.parquet"]
    seen.clear()
    env.query_pool(sort_by="S_mace")
    env.get_descriptors(ids=env.ids[:3])
    env.cheap_score(ids=env.ids[:3])
    env.fit_surrogate()
    env.calibrate_oracle(call_id="c0001")
    env.render_structure(id=env.ids[0], call_id="c0002")
    assert seen == []


def test_query_pool_filters_sort_and_limit(env):
    out = env.query_pool(sort_by="pld_A", ascending=False, limit=40, filters={"oms": True, "pld_max": 7})
    rows = [dict(zip(out.result["columns"], r)) for r in out.result["rows"]]
    assert len(rows) <= 25 and all(r["oms"] and r["pld_A"] <= 7 for r in rows)
    assert [r["pld_A"] for r in rows] == sorted((r["pld_A"] for r in rows), reverse=True)
    env.request_dft(ids=[rows[0]["id"]], rationale="r")
    again = env.query_pool(sort_by="pld_A", ascending=False, filters={"oms": True, "pld_max": 7})
    assert rows[0]["id"] not in again.ids
    bad = env.call("query_pool", {"sort_by": "E_CO2", "filters": {}}, "c0001")
    assert not bad.ok
    bad = env.call("query_pool", {"sort_by": "S_mace", "filters": {"secret": 1}}, "c0001")
    assert not bad.ok


def test_fit_surrogate_needs_five_labels_and_is_deterministic(env):
    env.request_dft(ids=env.ids[:4], rationale="r")
    assert not env.call("fit_surrogate", {}, "c0001").ok
    env.request_dft(ids=env.ids[4:12], rationale="r")
    a = env.fit_surrogate(kappa=1.0, top_k=10)
    b = env.fit_surrogate(kappa=1.0, top_k=10)
    assert a.result == b.result and len(a.ids) == 10 and not set(a.ids) & set(env.bought)


def test_calibrate_oracle_and_images(env, tmp_path):
    assert not env.call("calibrate_oracle", {}, "c0001").ok
    env.request_dft(ids=env.ids[:20], rationale="r")
    out = env.calibrate_oracle(call_id="c0002")
    assert set(out.result) >= {"n", "spearman", "auc"} and out.result["n"] == 20
    png = (tmp_path / out.image).read_bytes()
    assert png[:4] == b"\x89PNG" and _size(png) == (512, 512)
    r = env.render_structure(id=env.ids[0], call_id="c0003")
    assert r.ok and _size((tmp_path / r.image).read_bytes()) == (512, 512)
    assert r.result["site_neighbours"]


def test_tool_schemas_per_arm():
    assert "fit_surrogate" not in ARM_TOOLS["A6"]
    assert not {"fit_surrogate", "calibrate_oracle"} & set(ARM_TOOLS["A4"])
    names = [t["function"]["name"] for t in tool_schemas(ARM_TOOLS["A5"])]
    assert "request_dft" not in names


def _size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def test_similar_to_returns_unbought_neighbours_with_shared_metals():
    import tempfile

    from corehelp import FIXTURE_DATA

    from fortyshots.tools import ARM_TOOLS, PoolEnv

    env = PoolEnv(FIXTURE_DATA, "P0", tempfile.mkdtemp(), tools=ARM_TOOLS["A5"])
    ref = env.df["id"].iloc[0]
    out = env.call("similar_to", {"id": ref, "limit": 5}, "c0001")
    assert out.ok and len(out.ids) == 5 and ref not in out.ids
    dist = [r[1] for r in out.result["rows"]]
    assert dist == sorted(dist)
    assert "similar_to" not in ARM_TOOLS["A4"] and "similar_to" in ARM_TOOLS["A6"]
    q = env.call("query_pool", {"sort_by": ' "S_mace"', "limit": 3}, "c0002")
    assert q.ok
