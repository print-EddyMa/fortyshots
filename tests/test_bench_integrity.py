"""Regression tests for the benchmark integrity fixes: ledger-checked purchases, MACE coverage refusal,
PREREG 6.2 enrichment, harness-pick share, prereg-v1 provenance and the vision-only Critic metric."""
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from bench import arms, common, ingest, results, schema, surrogate, verify
from bench.common import BUDGET, ROOT, TEST_POOLS, MaceCoverageError, load_data
from bench.synthetic import make_data, make_mock_runs, write_run

A3_SAMPLE = ["--a3-sample", "1"]


@pytest.fixture(autouse=True)
def few_trees(monkeypatch):
    monkeypatch.setattr(surrogate, "N_TREES", 20)


@pytest.fixture(scope="module")
def base_root(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(surrogate, "N_TREES", 20)
    root = tmp_path_factory.mktemp("integrity")
    make_data(root)
    data = load_data(root / "data")
    (root / "results").mkdir()
    arms.run_all(data, TEST_POOLS, 2, log=lambda *_: None).to_parquet(root / "results/baselines.parquet")
    make_mock_runs(root, data)
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    yield root
    mp.undo()


@pytest.fixture
def root(base_root, tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(base_root, dst)
    return dst


def run_dir(root, prefix):
    return next(d for d in sorted((root / "runs").iterdir()) if d.name.startswith(prefix))


def labels(root):
    data = load_data(root / "data")
    hit_of = dict(zip(data.labels.id, data.labels.hit.astype(bool)))
    pool_of = dict(zip(data.universe.id, data.universe.pool))
    return data, hit_of, pool_of


def forge(d, fn):
    """Apply fn(summary, events) and write both back, keeping the final summary event equal to summary.json
    (a careful forger)."""
    summary = json.loads((d / "summary.json").read_text())
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines()]
    fn(summary, events)
    for e in events:
        if e["kind"] == "summary":
            e["payload"] = summary
    (d / "summary.json").write_text(json.dumps(summary))
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def set_picks(summary, picks, hit_of):
    summary.update(picks=picks, hit_flags=[bool(hit_of[i]) for i in picks], hits=sum(hit_of[i] for i in picks),
                   spent=len(picks), p_hits=[None] * len(picks))


def rewrite_ledger(events, picks, hit_of):
    """Replace the dft_order/dft_result events by ones that buy `picks` in batches of 10."""
    keep = [e for e in events if e["kind"] not in ("dft_order", "dft_result", "summary")]
    tail = [e for e in events if e["kind"] == "summary"]
    for k in range(0, len(picks), 10):
        b = picks[k:k + 10]
        keep.append({"seq": 0, "round": k // 10 + 1, "role": "system", "kind": "dft_order",
                     "payload": {"ids": b, "rationale": "", "spent": k + len(b), "remaining": 0}})
        keep.append({"seq": 0, "round": k // 10 + 1, "role": "system", "kind": "dft_result",
                     "payload": {"results": [{"id": i, "hit": bool(hit_of[i])} for i in b], "spent": k + len(b)}})
    events[:] = keep + tail
    for n, e in enumerate(events):
        e["seq"] = n


# ---- 1. forged summaries --------------------------------------------------------------------------------


def reviewer_forgery(summary, events, data, hit_of):
    """The confirmed reproduction: 20 copies of one P1 hit plus 10 P2 hits, summary only."""
    p1_hit = next(i for i in data.pool("P1").id if hit_of[i])
    p2_hits = [i for i in data.pool("P2").id if hit_of[i]][:10]
    set_picks(summary, [p1_hit] * 20 + p2_hits, hit_of)


def forged_ledger_too(summary, events, data, hit_of):
    """Duplicates and foreign-pool ids written into the ledger as well as the summary."""
    picks = summary["picks"][:20] + summary["picks"][:5] + list(data.pool("P2").id[:5])
    rewrite_ledger(events, picks, hit_of)
    set_picks(summary, picks, hit_of)


def reordered(summary, events, data, hit_of):
    """Hits moved to the front of the purchase order (better first-hit and early curve)."""
    picks = sorted(summary["picks"], key=lambda i: not hit_of[i])
    assert picks != summary["picks"]
    set_picks(summary, picks, hit_of)


def flags_flipped(summary, events, data, hit_of):
    summary["hit_flags"] = [True] * len(summary["picks"])
    summary["hits"] = len(summary["picks"])


def arm_relabelled(summary, events, data, hit_of):
    """An A4 run passed off as A5 (the header still says A4)."""
    summary["arm"] = "A5"


def seed_changed(summary, events, data, hit_of):
    summary["seed"] = 7


def over_budget(summary, events, data, hit_of):
    bought = set(summary["picks"])
    picks = summary["picks"] + [i for i in data.pool(summary["pool"]).id if i not in bought][:10]
    rewrite_ledger(events, picks, hit_of)
    set_picks(summary, picks, hit_of)


def result_hit_forged(summary, events, data, hit_of):
    """The dft_result rows claim a miss was a hit."""
    for e in events:
        if e["kind"] == "dft_result":
            for row in e["payload"]["results"]:
                if not hit_of[row["id"]]:
                    row["hit"] = True
                    return


def order_without_request(summary, events, data, hit_of):
    """A dft_result that returns ids its dft_order never asked for."""
    orders = [e for e in events if e["kind"] == "dft_order"]
    orders[0]["payload"]["ids"] = orders[0]["payload"]["ids"][:5]


def no_header(summary, events, data, hit_of):
    events[:] = [e for e in events if e["kind"] != "header"]


# kind: (forgery, run it is applied to (one run per kind), expected mismatch text)
FORGERIES = {
    "reviewer_repro": (reviewer_forgery, "A5-P1-s0", "differ from the ledger"),
    "ledger_duplicates_foreign_pool": (forged_ledger_too, "A5-P1-s1", "duplicate"),
    "reordered": (reordered, "A6-P1-s0", "differ from the ledger"),
    "hit_flags": (flags_flipped, "A6-P2-s1", "hit_flags differ from labels"),
    "arm_relabelled": (arm_relabelled, "A4-P1-s0", "header arm"),
    "seed_changed": (seed_changed, "A6-P2-s0", "header seed"),
    "over_budget": (over_budget, "A5-P2-s1", "exceed the header budget"),
    "dft_result_hit": (result_hit_forged, "A6-P1-s1", "dft_result hit flag"),
    "unrequested_result": (order_without_request, "A5-P2-s0", "did not request"),
    "no_header": (no_header, "A4-P2-s1", "no header event"),
}


def apply_forgery(root, kind):
    fn, prefix, _ = FORGERIES[kind]
    data, hit_of, _ = labels(root)
    d = run_dir(root, f"mock-{prefix}")
    forge(d, lambda s, e: fn(s, e, data, hit_of))
    return d


@pytest.mark.parametrize("kind", sorted(FORGERIES))
def test_forged_run_is_a_mismatch(root, kind):
    d = apply_forgery(root, kind)
    _, hit_of, pool_of = labels(root)
    r = ingest.load_run(d, hit_of, pool_of)
    assert any(FORGERIES[kind][2] in m for m in r.mismatches), r.mismatches
    assert r.excluded
    assert verify.check_runs([r]) and all(m.startswith(f"run {d.name}: ") for m in verify.check_runs([r]))


def test_reviewer_forgery_fails_verify(root):
    """The confirmed reproduction, with results.json regenerated by the forger so the diff alone is clean."""
    apply_forgery(root, "reviewer_repro")
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    res = json.loads((root / "results/results.json").read_text())
    assert {a["arm"]: a for a in res["arms"]}["A5"]["n_runs"] == 3  # the forged run is left out
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 1


def test_every_forgery_kind_fails_verify(root, capsys):
    for kind in FORGERIES:
        apply_forgery(root, kind)
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    res = json.loads((root / "results/results.json").read_text())
    assert sum(a["n_runs"] for a in res["arms"] if a["kind"] == "llm") == 12 - len(FORGERIES)
    capsys.readouterr()
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 1
    out = capsys.readouterr().out
    flagged = {line.split()[2].rstrip(":") for line in out.splitlines() if line.startswith("MISMATCH run ")}
    assert flagged == {run_dir(root, f"mock-{prefix}").name for _, prefix, _ in FORGERIES.values()}
    assert "MISMATCH /" not in out  # results.json itself recomputes exactly; only the runs fail


def test_reviewer_forgery_cannot_inflate_hits(root):
    """The analysis uses the ledger purchase order, so the forged summary changes nothing but n_runs."""
    data, hit_of, pool_of = labels(root)
    d = run_dir(root, "mock-A5-P1-s0")
    honest = ingest.load_run(d, hit_of, pool_of)
    forge(d, lambda s, e: reviewer_forgery(s, e, data, hit_of))
    r = ingest.load_run(d, hit_of, pool_of)
    assert r.picks == honest.picks and r.hit_flags == honest.hit_flags
    assert len(set(r.summary["picks"])) < len(r.summary["picks"])


def test_honest_runs_have_no_mismatches(base_root):
    _, hit_of, pool_of = labels(base_root)
    runs = ingest.load_runs(base_root / "runs", hit_of, include_mock=True, pool_of=pool_of)
    assert len(runs) == 13
    assert all(r.mismatches == [] and r.excluded is None for r in runs), [r.mismatches for r in runs]


def test_summary_picks_are_not_trusted_when_ledger_is_empty(root):
    """A summary whose ledger has no DFT events at all buys nothing."""
    _, hit_of, pool_of = labels(root)
    d = run_dir(root, "mock-A5-P1-s0")
    events = [line for line in (d / "events.jsonl").read_text().splitlines()
              if json.loads(line)["kind"] not in ("dft_order", "dft_result")]
    (d / "events.jsonl").write_text("\n".join(events) + "\n")
    r = ingest.load_run(d, hit_of, pool_of)
    assert r.picks == [] and r.excluded and any("differ from the ledger" in m for m in r.mismatches)


def test_non_40_budget_run_is_excluded_not_a_mismatch(root):
    data, hit_of, _ = labels(root)
    picks = list(data.pool("P1").id[:20])
    write_run(root, "A5", "P1", 3, picks, hit_of, budget=20)
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    res = json.loads((root / "results/results.json").read_text())
    assert {a["arm"]: a for a in res["arms"]}["A5"]["n_runs"] == 4
    r = ingest.load_run(run_dir(root, "mock-A5-P1-s3"), hit_of)
    assert r.mismatches == [] and "budget 20" in r.excluded
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 0


# ---- 2. MACE coverage -----------------------------------------------------------------------------------


def drop_mace(root, pool, how):
    data = load_data(root / "data")
    victim = data.pool(pool).id.iloc[3]
    path = root / "data/mace_cache.parquet"
    m = pd.read_parquet(path)
    if how == "nan":
        m.loc[m.id == victim, "S_mace"] = np.nan
    else:
        m = m[m.id != victim]
    m.to_parquet(path, index=False)


@pytest.mark.parametrize("how", ["nan", "missing_row"])
def test_results_refuse_incomplete_mace(root, how, capsys):
    drop_mace(root, "P3", how)
    with pytest.raises(MaceCoverageError, match="P3"):
        results.compute(root, include_mock=True)
    before = (root / "results/results.json").read_text()
    assert results.main(["--root", str(root), "--include-mock"]) == 2
    assert "incomplete MACE values" in capsys.readouterr().err
    assert (root / "results/results.json").read_text() == before
    # A subset of complete pools still works, and records its coverage.
    assert results.main(["--root", str(root), "--include-mock", "--pools", "P1", "P2"]) == 0
    res = json.loads((root / "results/results.json").read_text())
    assert [(p["pool"], p["mace_coverage"]) for p in res["pools"]] == [("P1", 1.0), ("P2", 1.0)]
    assert schema.validate_results(res) == []


def test_verify_refuses_incomplete_mace(root, capsys):
    drop_mace(root, "P4", "nan")
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 2
    assert "refused" in capsys.readouterr().out


def test_mace_coverage_fraction(root):
    drop_mace(root, "P2", "missing_row")
    data = load_data(root / "data")
    assert data.mace_coverage("P2") == pytest.approx(1 - 1 / len(data.pool("P2")))
    assert data.mace_coverage("P1") == 1.0


@pytest.mark.parametrize("arm", ["A2", "A3"])
def test_mace_arms_refuse_nan_instead_of_ranking_it_last(root, arm):
    drop_mace(root, "P1", "nan")
    with pytest.raises(MaceCoverageError):
        arms.run_arm(arm, load_data(root / "data").pool("P1"), 0)


def test_verify_reruns_a3_on_every_pool(root):
    path = root / "results/baselines.parquet"
    base = pd.read_parquet(path)
    sel = (base.arm == "A3") & (base.pool == "P4") & (base.seed == 0)
    orders = base.loc[sel, "order"].to_numpy()
    base.loc[sel, "order"] = np.r_[orders[-1], orders[:-1]]  # rotate the purchase sequence
    base.to_parquet(path, index=False)
    problems = verify.check_baselines(root, 1, TEST_POOLS)
    assert problems == ["baselines A3 P4 seed 0: purchase sequence differs"]
    problems = verify.check_baselines(root, 1, ["P1", "P2"])
    assert problems == []


def test_verify_flags_missing_baselines_for_an_analysed_pool(root):
    path = root / "results/baselines.parquet"
    base = pd.read_parquet(path)
    base[~((base.pool == "P5") & (base.arm == "A1"))].to_parquet(path, index=False)
    assert "baselines A1 P5: no runs in results/baselines.parquet" in verify.check_baselines(root, 0, TEST_POOLS)


# ---- 3. enrichment --------------------------------------------------------------------------------------


def test_enrichment_is_mean_of_per_pool_ratios():
    pool_hits, pool_n = {"P1": 10, "P2": 20}, {"P1": 500, "P2": 500}
    hits = {"P1": np.array([1.0, 3.0]), "P2": np.array([2.0])}
    # P1: 2 / (40 * 0.02) = 2.5; P2: 2 / (40 * 0.04) = 1.25 -> 1.875 (the old ratio of means gave 1.667).
    assert results.enrichment(hits, pool_hits, pool_n) == pytest.approx(1.875)
    assert results.enrichment(hits, {"P1": 10, "P2": 0}, pool_n) is None


def test_results_enrichment_matches_prereg(base_root):
    res = json.loads((base_root / "results/results.json").read_text())
    rate = {p["pool"]: p["hits"] / p["n"] for p in res["pools"]}
    base = pd.read_parquet(base_root / "results/baselines.parquet")
    per_pool = base[base.arm == "A0"].groupby(["pool", "seed"]).hit.sum().groupby("pool").mean()
    want = np.mean([per_pool[p] / (BUDGET * rate[p]) for p in rate])
    a0 = next(a for a in res["arms"] if a["arm"] == "A0")
    assert a0["enrichment"] == pytest.approx(want, abs=1e-4)


# ---- 4. harness picks -----------------------------------------------------------------------------------


def test_harness_share_and_missing_key(root):
    _, hit_of, pool_of = labels(root)
    r = ingest.load_run(run_dir(root, "mock-A5-P2-s1"), hit_of, pool_of)
    assert r.harness_picks == 10 and r.harness_share == 0.25
    old = ingest.load_run(run_dir(root, "mock-A4-P1-s0"), hit_of, pool_of)
    assert "harness_picks" not in old.summary and old.harness_picks == 0 and old.harness_share == 0.0
    d = run_dir(root, "mock-A6-P1-s0")
    forge(d, lambda s, e: s.update(harness_pick_ids=s["harness_pick_ids"] + ["mzzzzzzz"]))
    assert any("harness_pick_ids" in m for m in ingest.load_run(d, hit_of, pool_of).mismatches)


# ---- 5. prereg-v1 provenance ----------------------------------------------------------------------------

TAG = "a" * 40


def add_real_run(root, pool, seed, git_sha):
    data, hit_of, _ = labels(root)
    write_run(root, "A5", pool, seed, list(data.pool(pool).id[:BUDGET]), hit_of, mock=False, git_sha=git_sha)


@pytest.fixture
def tagged(monkeypatch):
    """Pretend tag prereg-v1 is commit TAG whose descendants are TAG and 'b' * 40."""
    for mod in (results, verify):
        monkeypatch.setattr(mod, "prereg_commit", lambda root, *a: TAG)
    monkeypatch.setattr(verify, "is_descendant", lambda root, anc, sha: anc == TAG and sha in (TAG, "b" * 40))


def test_prereg_sha_is_tag_commit_or_unfrozen(root, monkeypatch):
    res, _, _ = results.compute(root, include_mock=True)
    assert res["prereg_sha"] == "unfrozen"
    monkeypatch.setattr(results, "prereg_commit", lambda root, *a: TAG)
    assert results.compute(root, include_mock=True)[0]["prereg_sha"] == TAG


def test_provenance_flags_runs_before_the_tag(root, tagged):
    add_real_run(root, "P1", 5, "b" * 40)
    add_real_run(root, "P2", 5, "c" * 40)
    add_real_run(root, "P0", 5, "c" * 40)  # dev pool, not analysed: never checked
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    _, hit_of, pool_of = labels(root)
    runs = ingest.load_runs(root / "runs", hit_of, include_mock=True, pool_of=pool_of)
    problems, note = verify.check_provenance(root, runs, TEST_POOLS)
    assert note.startswith("git_sha provenance checked")
    want = f"run A5-P2-s5-202610301200: header git_sha {'c' * 40!r} is not prereg-v1 ({TAG[:12]}) or a descendant"
    assert problems == [want]
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 1


def test_provenance_passes_for_descendants_and_skips_without_tag(root, tagged, monkeypatch):
    add_real_run(root, "P1", 5, TAG)
    add_real_run(root, "P3", 5, "b" * 40)
    assert results.main(["--root", str(root), "--include-mock"]) == 0
    assert verify.main(["--root", str(root), *A3_SAMPLE]) == 0
    for mod in (results, verify):
        monkeypatch.setattr(mod, "prereg_commit", lambda root, *a: None)
    _, hit_of, pool_of = labels(root)
    runs = ingest.load_runs(root / "runs", hit_of, include_mock=True, pool_of=pool_of)
    assert verify.check_provenance(root, runs, TEST_POOLS)[0] == []


def git_out(*args):
    """stdout of a read-only git command on this repository, or "" when git or the history is unavailable."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], check=False, capture_output=True, text=True)
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


@pytest.mark.skipif(not git_out("rev-parse", "HEAD"), reason="repository history not available")
def test_git_helpers_read_only_on_this_repo():
    head = git_out("rev-parse", "HEAD")
    first = git_out("rev-list", "--max-parents=0", "HEAD").split()[0]
    assert common.prereg_commit(ROOT, tag="no-such-tag-xyz") is None
    assert common.is_descendant(ROOT, first, head) and common.is_descendant(ROOT, head, head)
    assert not common.is_descendant(ROOT, head, first) or head == first
    assert not common.is_descendant(ROOT, first, "f" * 40) and not common.is_descendant(ROOT, first, None)


# ---- 6. Critic metric -----------------------------------------------------------------------------------


def test_critic_uses_only_vision_critiques(tmp_path):
    make_data(tmp_path)
    data, hit_of, pool_of = labels(tmp_path)
    picks = list(data.pool("P1").id[:BUDGET])
    write_run(tmp_path, "A5", "P1", 0, picks, hit_of, critiques=[(0.1, 0.05), (0.2, 0.3)], vision=True)
    write_run(tmp_path, "A5", "P1", 1, picks, hit_of, critiques=[(0.9, 0.0), (0.9, 0.0)], vision=False)
    d = run_dir(tmp_path, "mock-A5-P1-s1")
    old = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines()]
    for e in old:
        if e["kind"] == "critique":
            del e["payload"]["vision"]  # runs older than the key count as text-only
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in old))
    runs = ingest.load_runs(tmp_path / "runs", hit_of, include_mock=True, pool_of=pool_of)
    assert results.critic_block(runs, ["P1"]) == {"visual_rho_mae": pytest.approx(0.075), "n": 2, "n_text": 2}
    text_only = [r for r in runs if r.seed == 1]
    assert results.critic_block(text_only, ["P1"]) == {"visual_rho_mae": None, "n": 0, "n_text": 2}


# ---- schema ---------------------------------------------------------------------------------------------


def test_schema_optional_fields(base_root):
    res = json.loads((base_root / "results/results.json").read_text())
    assert schema.validate_results(res) == []
    stripped = json.loads(json.dumps(res))
    for p in stripped["pools"]:
        del p["mace_coverage"]
    for a in stripped["arms"]:
        a.pop("harness_share", None)
    del stripped["critic"]["n_text"]
    stripped["tests"][0]["ci_note"] = "conditional on pools"
    assert schema.validate_results(stripped) == []
    assert schema.validate_results({**res, "critic": {"visual_rho_mae": None, "n": 0, "n_text": 2}}) == []
    bad = json.loads(json.dumps(res))
    bad["pools"][0]["mace_coverage"] = "full"
    bad["arms"][-1]["harness_share"] = "x"
    errs = schema.validate_results(bad)
    assert len(errs) == 2 and any("mace_coverage" in e for e in errs)
