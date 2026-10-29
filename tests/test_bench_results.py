import json

import pytest

from bench import arms, results, schema, surrogate, verify
from bench.common import ROOT, TEST_POOLS, load_data
from bench.synthetic import make_data, make_mock_runs


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(surrogate, "N_TREES", 20)
    root = tmp_path_factory.mktemp("repo")
    make_data(root)
    data = load_data(root / "data")
    (root / "results").mkdir()
    arms.run_all(data, TEST_POOLS, 4, log=lambda *_: None).to_parquet(root / "results/baselines.parquet")
    make_mock_runs(root, data)
    results.main(["--root", str(root), "--include-mock"])
    yield root
    mp.undo()


def load(root, name):
    return json.loads((root / "results" / name).read_text())


def test_schema_valid(root):
    res, pools = load(root, "results.json"), load(root, "pools.json")
    assert schema.validate_results(res) == []
    assert schema.validate_pools(pools) == []
    assert res["status"] == "mock"
    assert [a["arm"] for a in res["arms"]] == ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]


def test_contents(root):
    res = load(root, "results.json")
    arm = {a["arm"]: a for a in res["arms"]}
    assert arm["A0"]["n_runs"] == 5 * 4 and arm["A5"]["n_runs"] == 2 * 2
    for a in arm.values():
        assert a["curve"][0] == 0 and a["curve"][-1] == pytest.approx(a["hits_mean"], abs=1e-4)
        assert a["hits_ci"][0] <= a["hits_mean"] + 1e-9 <= a["hits_ci"][1] + 2e-9
        assert all(lo <= hi for lo, hi in zip(a["curve_lo"], a["curve_hi"]))
    names = [t["name"] for t in res["tests"]]
    assert names[0].startswith("primary: A5 - A3")
    assert all(0 < t["p"] <= 1 for t in res["tests"])
    assert res["calibration"]["n"] == 2 * 2 * 40
    assert res["critic"] == {"visual_rho_mae": 0.075, "n": 16, "n_text": 0}
    assert res["claims"] == {"n": 120, "verified": 108, "flagged": 12}
    assert res["leak"]["L_auc"] is not None and res["leak"]["L2_auc"] is None
    assert [c["pool"] for c in res["ceilings"]] == TEST_POOLS
    assert res["prereg_sha"] == "unfrozen"  # no prereg-v1 tag in the synthetic root; never a run header value
    assert all(p["mace_coverage"] == 1.0 for p in res["pools"])
    # Mock A5/A6 runs log 5 (seed 0) and 10 (seed 1) of 40 purchases as harness picks; A4 runs carry no key.
    assert arm["A5"]["harness_share"] == arm["A6"]["harness_share"] == pytest.approx((0.125 + 0.25) / 2)
    assert arm["A4"]["harness_share"] == 0.0
    assert not any("harness_share" in arm[a] for a in ("A0", "A1", "A2", "A3"))


def test_no_mock_by_default(root, tmp_path):
    res, _, runs = results.compute(root, include_mock=False)
    assert runs == [] and res["status"] == "pilot"
    assert {a["kind"] for a in res["arms"]} == {"baseline"}
    assert res["calibration"] is None and res["claims"] is None


def test_verify_passes_then_catches_tampering(root):
    mp = pytest.MonkeyPatch()
    mp.setattr(surrogate, "N_TREES", 20)
    try:
        assert verify.main(["--root", str(root)]) == 0
        path = root / "results" / "results.json"
        good = path.read_text()
        res = json.loads(good)
        res["arms"][0]["hits_mean"] += 0.01
        path.write_text(json.dumps(res))
        assert verify.main(["--root", str(root)]) == 1
        path.write_text(good)
        assert verify.main(["--root", str(root)]) == 0
    finally:
        mp.undo()


def test_verify_diff_tolerance():
    assert verify.diff({"a": 1.0, "generated": "x"}, {"a": 1.0001, "generated": "y"}) == []
    assert verify.diff({"a": [1, 2]}, {"a": [1]}) != []
    assert verify.diff({"a": True}, {"a": 1}) != []


def test_schema_rejects_bad_shapes():
    assert schema.validate_results({"status": "nope"})
    bad_pools = {"pools": {p: [] for p in schema.POOL_NAMES}}
    bad_pools["pools"]["P1"] = [{"id": "m1"}]
    assert schema.validate_pools(bad_pools)


REAL = ROOT / "results" / "results.json"


@pytest.mark.skipif(not REAL.exists(), reason="results/results.json not built")
def test_real_results_schema():
    assert schema.validate_results(json.loads(REAL.read_text())) == []
    assert schema.validate_pools(json.loads((ROOT / "results" / "pools.json").read_text())) == []
