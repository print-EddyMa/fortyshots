"""Prospective mode, Mac side: the Chemist may only propose specs physics/mutate.py can execute."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import FIXTURE_DATA, events, make_cfg, run

from fortyshots import chemist as C
from fortyshots import cli
from fortyshots.roles import prompts as P
from fortyshots.roles import schemas as S

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "physics"))
import mutate  # noqa: E402  (numpy-only at import time)


def benzene_with_zn(cell=12.0):
    ang = np.arange(6) * np.pi / 3
    c = np.c_[1.397 * np.cos(ang), 1.397 * np.sin(ang), np.zeros(6)] + cell / 2
    h = np.c_[2.481 * np.cos(ang), 2.481 * np.sin(ang), np.zeros(6)] + cell / 2
    pos = np.vstack([c, h, [[1.0, 1.0, 1.0]]])
    return {"cell": (np.eye(3) * cell).tolist(), "numbers": [6] * 6 + [1] * 6 + [30], "pos": pos.tolist(), "ads": []}


def test_constants_match_the_builder():
    assert C.GROUPS == mutate.GROUPS and C.SITES == mutate.SITES
    assert [set(f) for f in C.SWAP_FAMILIES] == [set(f) for f in mutate.SWAP_FAMILIES]
    assert C.MAX_PARENT_ATOMS == mutate.MAX_PARENT_ATOMS
    assert sorted(C.swap_partners("Zn")) == ["Co", "Mg", "Ni"] and C.swap_partners("Fe") == ["Al", "Sc"]
    assert C.swap_partners("Cu") == []


def test_aromatic_ch_count_and_periodic_images():
    s = benzene_with_zn()
    assert C.aromatic_ch_count(s) == 6
    # adsorbate atoms are ignored; a ring split across the boundary still counts
    co2 = [[3, 3, 3], [3, 3, 4.16], [3, 3, 1.84]]
    s2 = dict(s, numbers=s["numbers"] + [6, 8, 8], pos=s["pos"] + co2, ads=[13, 14, 15])
    assert C.aromatic_ch_count(s2) == 6
    shifted = dict(s, pos=((np.asarray(s["pos"]) + [6.0, 0, 0]) % 12.0).tolist())
    assert C.aromatic_ch_count(shifted) == 6
    lone = dict(s, pos=[list(x) for x in s["pos"]])
    lone["pos"][6] = [6.0, 6.0, 9.0]  # an H with no neighbour is not a site
    assert C.aromatic_ch_count(lone) == 5


def test_options_and_parents(tmp_path):
    (tmp_path / "structures").mkdir()
    (tmp_path / "structures" / "m0000001.json").write_text(json.dumps(benzene_with_zn()))
    bare = dict(benzene_with_zn(), numbers=[6] * 6 + [8] * 6 + [30])  # no H at all
    (tmp_path / "structures" / "m0000002.json").write_text(json.dumps(bare))
    metals = {"m0000001": "Zn", "m0000002": "Cu", "m0000003": "Fe,Cu"}
    opts = C.options(["m0000001", "m0000002", "m0000003"], metals, tmp_path)
    assert opts["m0000001"] == {"sites": 6, "swaps": {"Zn": ["Mg", "Co", "Ni"]}}
    assert "m0000002" not in opts  # no aromatic C-H and no swappable metal: nothing to propose
    assert opts["m0000003"] == {"sites": None, "swaps": {"Fe": ["Al", "Sc"]}}
    bought = {"a": {"hit": False, "S": -1.0}, "b": {"hit": True, "S": -0.6}, "c": {"hit": True, "S": -0.9},
              "d": {"hit": True, "S": -2.0}}
    assert C.choose_parents(bought, list(bought), {"a": 50, "b": 60, "c": 70, "d": 500}) == ["c", "b", "a"]


def _variants(schema):
    return schema["properties"]["mutations"]["items"]["anyOf"]


def test_every_schema_choice_is_executable_and_nothing_else():
    opts = {"m0000001": {"sites": 6, "swaps": {"Zn": ["Mg", "Co", "Ni"]}},
            "m0000002": {"sites": 0, "swaps": {"Al": ["Fe", "Sc"]}},
            "m0000003": {"sites": None, "swaps": {}}}
    schema = S.mutations_schema(opts)
    assert schema["properties"]["mutations"]["maxItems"] == C.MAX_MUTATIONS
    n = 0
    for v in _variants(schema):
        pr = v["properties"]
        parents = pr["parent"].get("enum") or [pr["parent"]["const"]]
        if pr["op"]["const"] == "functionalize":
            assert parents == ["m0000001", "m0000003"]  # m0000002 has no aromatic C-H site
            combos = [dict(parent=p, op="functionalize", group=g, site=s)
                      for p in parents for g in pr["group"]["enum"] for s in pr["site"]["enum"]]
        else:
            combos = [dict(parent=parents[0], op="swap_metal", from_metal=pr["from_metal"]["const"], to_metal=t)
                      for t in pr["to_metal"]["enum"]]
        for c in combos:
            m = S.Mutation(**c, rationale="x")
            assert C.check(m, opts) is None, c
            spec = C.to_spec(m)
            assert mutate.normalize(spec) == spec  # already in builder form
            n += 1
    assert n == 2 * 4 * 3 + 3 + 2
    bad = [dict(parent="m0000001", op="functionalize", group="NO2", site="near_co2"),
           dict(parent="m0000001", op="functionalize", group="NH2", site="aromatic C-H"),
           dict(parent="m0000002", op="functionalize", group="F", site="near_metal"),
           dict(parent="m0000001", op="swap_metal", from_metal="Zn", to_metal="Al"),
           dict(parent="m0000001", op="swap_metal", from_metal="Cu", to_metal="Zn"),
           dict(parent="m9999999", op="swap_metal", from_metal="Zn", to_metal="Mg"),
           dict(parent="m0000001", op="metal_swap")]
    for c in bad:
        assert C.check(S.Mutation(**c), opts), c


def test_legacy_specs_are_normalized():
    assert mutate.normalize({"kind": "functionalize", "site": "aromatic C-H", "group": "NH2", "new_metal": ""}) == {
        "op": "functionalize", "group": "NH2", "site": "near_co2"}
    assert mutate.normalize({"kind": "metal_swap", "new_metal": "Mg"}, ["Zn", "O", "C"]) == {
        "op": "swap_metal", "from": "Zn", "to": "Mg"}
    with pytest.raises(ValueError):
        mutate.normalize({"kind": "metal_swap", "new_metal": "Mg"}, ["Zn", "Co"])  # which one?


def test_prompt_lists_what_each_parent_allows():
    opts = {"m0000001": {"sites": 6, "swaps": {"Zn": ["Mg", "Co", "Ni"]}}, "m0000003": {"sites": None, "swaps": {}}}
    text = P.CHEMIST.format(parents=C.describe(["m0000001 C6H6Zn Zn -0.9 HIT", "m0000003 X Fe,Cu 0.1 -"], opts),
                            n=C.MAX_MUTATIONS)
    assert "m0000001 C6H6Zn Zn -0.9 HIT | aromatic C-H sites: 6 | metal swaps: Zn->Mg/Co/Ni" in text
    assert "aromatic C-H sites: unknown | metal swaps: none" in text
    assert "near_co2" in text and "at most 5" in text


def test_chemist_logs_only_executable_specs(tmp_path):
    def hook(req, resp):
        rf = req.get("response_format") or {}
        if (rf.get("json_schema") or {}).get("name") != "Mutations":
            return None
        variants = rf["json_schema"]["schema"]["properties"]["mutations"]["items"]["anyOf"]
        good = []
        for v in variants:
            pr = v["properties"]
            if pr["op"]["const"] == "functionalize":
                good.append({"parent": pr["parent"]["enum"][0], "op": "functionalize", "group": "OH",
                             "site": "near_metal", "rationale": "ok"})
        good.append({"parent": good[0]["parent"] if good else "m0000000", "op": "swap_metal", "from_metal": "Zn",
                     "to_metal": "Al", "rationale": "not charge preserving"})
        good.append(dict(good[0]) if len(good) > 1 else good[-1])
        resp["choices"][0]["message"]["content"] = json.dumps({"mutations": good})
        return resp

    from corehelp import MockTransport
    _, camp = run(make_cfg(tmp_path, chemist=True), transport=MockTransport(hook=hook))
    evs = events(camp.run_dir)
    assert evs[0]["payload"]["config"]["chemist"] is True
    muts = [e for e in evs if e["kind"] == "mutation"]
    skipped = [e for e in evs if e["kind"] == "error" and e["payload"]["where"] == "chemist"]
    assert len(skipped) >= 1 and all(e["payload"]["recovered"] for e in skipped)
    for e in muts:
        assert e["payload"]["status"] == "proposed" and e["role"] == "chemist"
        assert e["payload"]["spec"] == mutate.normalize(e["payload"]["spec"])
    assert evs[-1]["kind"] == "summary"


def test_chemist_is_off_by_default(tmp_path):
    _, camp = run(make_cfg(tmp_path))
    evs = events(camp.run_dir)
    assert evs[0]["payload"]["config"]["chemist"] is False
    assert not [e for e in evs if e["kind"] == "mutation" or e["role"] == "chemist"]


def test_chemist_plan_is_dev_pool_only():
    plan = json.loads((ROOT / "mac" / "plans" / "chemist.json").read_text())
    runs = [{**plan.get("defaults", {}), **r} for r in plan["runs"]]
    assert runs and all(r["arm"] == "A5" and r["pool"] == "P0" and r["seed"] >= 90 and r["chemist"] is True
                        for r in runs)


def test_record_honours_chemist_in_plan_entries(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"defaults": {"rounds": 1}, "runs": [{"arm": "A5", "pool": "P0", "seed": 90,
                                                                       "chemist": True},
                                                                      {"arm": "A5", "pool": "P0", "seed": 91}]}))
    monkeypatch.setattr(cli, "_transport", lambda cfg: __import__("corehelp").MockTransport())
    with pytest.raises(SystemExit) as ex:
        cli.main(["record", str(plan), "--mode", "mock", "--runs-dir", str(tmp_path / "runs"),
                  "--data-dir", str(FIXTURE_DATA), "--doctor-report", ""])
    assert ex.value.code == 0
    heads = {}
    for d in (tmp_path / "runs").iterdir():
        if d.is_dir():
            h = events(d)[0]["payload"]
            heads[h["seed"]] = h["config"]["chemist"]
    assert heads == {90: True, 91: False}
