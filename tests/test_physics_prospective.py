"""Prospective mode, container side: physics/prospective.py bookkeeping (sidecar, index, ranking, idempotency).

The MACE relaxation itself needs venvA and is exercised by the demo run, not here; these tests cover everything
around it with a synthetic run directory over the real data/ tables."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import FIXTURE_DATA, make_cfg, run

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "physics"))
import prospective  # noqa: E402

needs_data = pytest.mark.skipif(not (DATA / "labels.parquet").exists() or not (DATA / "descriptors.parquet").exists(),
                                reason="data/ tables not built")


def _ev(seq, kind, payload, role="system", rnd=0):
    return {"seq": seq, "t": "2026-10-30T00:00:00.000Z", "run_id": "mock-A5-P0-s90-202610300000", "round": rnd,
            "role": role, "kind": kind, "payload": payload, "call_id": None, "tokens": None, "timings": None,
            "dur_ms": None}


def synthetic_run(tmp_path):
    uni = pd.read_parquet(DATA / "universe.parquet")
    lab = pd.read_parquet(DATA / "labels.parquet").set_index("id")
    desc = pd.read_parquet(DATA / "descriptors.parquet").set_index("id")
    ids = [i for i in sorted(uni[uni.pool == "P0"].id) if i in desc.index][:12]
    rows = [{"id": i, "E_CO2": float(lab.at[i, "E_CO2"]), "E_H2O": float(lab.at[i, "E_H2O"]),
             "hit": bool(lab.at[i, "hit"])} for i in ids]
    parent = ids[0]
    evs = [_ev(0, "header", {"arm": "A5", "pool": "P0", "seed": 90, "budget": 12}),
           _ev(1, "dft_order", {"ids": ids, "rationale": "x", "spent": 12, "remaining": 0}, rnd=1),
           _ev(2, "dft_result", {"results": rows, "hits_total": sum(r["hit"] for r in rows), "spent": 12}, rnd=1),
           _ev(3, "mutation", {"parent": parent, "spec": {"op": "functionalize", "group": "NH2", "site": "near_co2"},
                               "status": "proposed", "note": "amine"}, role="chemist", rnd=2),
           _ev(4, "mutation", {"parent": parent, "spec": {"op": "swap_metal", "from": "Zn", "to": "Al"},
                               "status": "proposed", "note": "bad swap"}, role="chemist", rnd=2),
           _ev(5, "summary", {"hits": 0}, rnd=2)]
    d = tmp_path / "mock-A5-P0-s90-202610300000"
    (d / "mutants").mkdir(parents=True)
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
    (d / "summary.json").write_text(json.dumps({"hits": 0}))
    mid = f"{parent}-NH2s3"
    pd_row = desc.loc[parent]
    doc = {"id": mid, "parent": parent, "spec": evs[3]["payload"]["spec"], "label": prospective.LABEL,
           "cell": np.eye(3).tolist(), "numbers": [6, 1, 6, 8, 8], "ads": [2, 3, 4], "pos": [[0, 0, 0]] * 5,
           "pos_relaxed": [[0, 0, 0]] * 5, "frames": [{"cell": np.eye(3).tolist(), "pos": [[0, 0, 0]] * 5}] * 3,
           "n_steps": 40, "validity": {"clash_ratio": 0.9, "dV_frac": 0.01, "fmax": 0.04, "steps": 40,
                                        "converged": True, "charge": "n/a", "valid": True},
           "mace": {"E_CO2_mace": -0.4, "E_H2O_mace": -0.2, "S_mace": -0.3}, "eqv2": None,
           "descriptors": {**{k: (pd_row[k].item() if hasattr(pd_row[k], "item") else pd_row[k])
                              for k in prospective.DESC_KEYS}, "natoms": int(pd_row["natoms"]) + 2},
           "relax_s": 30.0}
    (d / "mutants" / f"{mid}.json").write_text(json.dumps(doc))
    side = [_ev(6, "mutation", {"parent": parent, "spec": doc["spec"], "status": "built", "note": "valid",
                                "proposal_seq": 3, "mutant_id": mid, "label": prospective.LABEL}, rnd=2),
            _ev(7, "mutation", {"parent": parent, "spec": evs[4]["payload"]["spec"], "status": "invalid",
                                "note": "Zn->Al is not a charge-preserving swap", "proposal_seq": 4,
                                "mutant_id": None, "label": prospective.LABEL}, rnd=2)]
    (d / "mutations.jsonl").write_text("".join(json.dumps(e) + "\n" for e in side))
    return d, mid, parent


@needs_data
def test_rerun_is_idempotent_and_never_touches_the_ledger(tmp_path):
    d, mid, parent = synthetic_run(tmp_path)
    ledger, side = (d / "events.jsonl").read_bytes(), (d / "mutations.jsonl").read_bytes()
    prospective.main([str(d), "--data", str(DATA)])  # nothing left to build: no MACE needed
    idx = json.loads((d / "mutants" / "index.json").read_text())
    assert (d / "events.jsonl").read_bytes() == ledger and (d / "mutations.jsonl").read_bytes() == side
    assert idx["label"] == "unvalidated · ML only" and "no ranking signal" in idx["mace_note"]
    assert idx["surrogate"]["n_train"] == 12 and idx["n_proposed"] == 2 and idx["n_built"] == 1
    good, bad = idx["mutants"]
    assert good["id"] == mid and good["rank"] == 1 and good["status"] == "built" and good["file"] == f"mutants/{mid}.json"
    assert np.isfinite(good["pred_S"]) and good["pred_std"] >= 0 and good["parent"] == parent
    assert good["spec_text"] == "functionalize NH2 at the C-H nearest the CO2 pocket (C-H #3)" and good["n_frames"] == 3
    assert good["parent_S"] is not None and good["mace"]["S_mace"] == -0.3 and good["label"] == prospective.LABEL
    assert bad["status"] == "invalid" and bad["rank"] is None and bad["file"] is None and "charge" in bad["reason"]
    assert bad["spec_text"] == "swap Zn → Al"
    first = {k: v for k, v in idx.items() if k not in ("generated", "wall_s")}
    prospective.main([str(d), "--data", str(DATA)])
    again = json.loads((d / "mutants" / "index.json").read_text())
    assert {k: v for k, v in again.items() if k not in ("generated", "wall_s")} == first
    assert (d / "mutations.jsonl").read_bytes() == side


@needs_data
def test_unfinished_runs_are_refused(tmp_path):
    d, _, _ = synthetic_run(tmp_path)
    (d / "summary.json").unlink()
    with pytest.raises(SystemExit):
        prospective.main([str(d), "--data", str(DATA)])


def test_invalid_reason_and_site_index():
    c = {"clash_ratio": 0.6, "dV_frac": 0.2, "fmax": 0.3, "steps": 300, "converged": False, "charge": "fail"}
    why = prospective.invalid_reason(c)
    assert "clash" in why and "volume" in why and "not converged" in why and "charge" in why
    assert prospective._site_index("m1234567-CH3s12") == 12 and prospective._site_index("m1234567-Zn2Mg") is None
    assert prospective.spec_text({"op": "functionalize", "group": "F", "site": 2}) == "functionalize F at 2"


def test_sidecar_keeps_bench_ingest_and_resume_clean(tmp_path):
    """A sidecar next to a finished ledger changes nothing for bench.ingest or for a resume of that run."""
    from bench.ingest import load_run

    from fortyshots.campaign import Campaign
    summary, camp = run(make_cfg(tmp_path, chemist=True), run_id="mock-A5-P0-s0-202610300000")
    d = camp.run_dir
    ledger = (d / "events.jsonl").read_bytes()
    (d / "mutants").mkdir()
    (d / "mutations.jsonl").write_text(json.dumps(_ev(10 ** 4, "mutation", {"status": "built"})) + "\n")
    lab = pd.read_parquet(FIXTURE_DATA / "labels.parquet")
    r = load_run(d, dict(zip(lab["id"], lab["hit"].astype(bool))))
    assert r.mismatches == []
    from corehelp import MockTransport
    again = Campaign(make_cfg(tmp_path, chemist=True), run_id=d.name, transport=MockTransport()).run()
    assert again["picks"] == summary["picks"] and (d / "events.jsonl").read_bytes() == ledger


def test_resolve_site_descriptors():
    pytest.importorskip("ase")
    pytest.importorskip("pymatgen")
    import ase
    import mutate
    from ase.build import molecule
    a = molecule("C6H6")
    a.set_cell([14, 14, 14])
    a.center()
    a.pbc = True
    a += ase.Atoms("Zn", positions=[a.positions[6] + [3.0, 0, 0]])  # next to the first H (index 6)
    sites = mutate.aromatic_ch_sites(a)
    h = [s[0] for s in sites]
    assert h[mutate.resolve_site(a, sites, "near_metal")] == 6
    far = mutate.resolve_site(a, sites, "far_from_metal")
    assert np.linalg.norm(a.positions[h[far]] - a.positions[-1]) == max(
        np.linalg.norm(a.positions[i] - a.positions[-1]) for i in h)
    co2 = [a.positions[h[2]] + [0, 0, 3.0], [0, 0, 0], [0, 0, 0]]
    assert mutate.resolve_site(a, sites, "near_co2", co2) == 2
    with pytest.raises(ValueError):
        mutate.resolve_site(a, sites, "near_co2", None)
    b, tag = mutate.build(a, {"op": "functionalize", "group": "F", "site": "near_metal"})
    assert tag == f"Fs{mutate.resolve_site(a, sites, 'near_metal')}" and len(b) == len(a)
