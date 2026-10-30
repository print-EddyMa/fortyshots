"""bench.fill_docs: every doc placeholder comes from measured data, mock numbers are refused, human-only
placeholders are never invented, and the pre-registered beats / ties / loses wording holds."""
import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from bench import arms, fill_docs, results, surrogate
from bench.common import BUDGET, TEST_POOLS, load_data
from bench.synthetic import make_data, make_mock_runs, write_run

DOC = """# Synthetic README
Status {{RESULTS_STATUS}}, prereg {{PREREG_SHA}}. Video: {{VIDEO_URL}}. Team {{TEAM_NAME}}.
> {{HEADLINE}}

| A0 | {{A0_N}} | {{A0_HITS}} [{{A0_LO}}, {{A0_HI}}] | {{A0_ENR}} | {{A0_LPH}} |
| A2 | {{A2_N}} | {{A2_HITS}} [{{A2_LO}}, {{A2_HI}}] | {{A2_ENR}} | {{A2_LPH}} |
| A5 | {{A5_N}} | {{A5_HITS}} [{{A5_LO}}, {{A5_HI}}] | {{A5_ENR}} | {{A5_LPH}} |
Primary: {{A5_A2_DIFF}}, p = {{A5_A2_P}}. Random {{RANDOM_HITS}}, oracle {{ORACLE_HITS}}.
Against random it {{VS_RANDOM}}; against MACE-greedy it {{VS_A2: beats / ties / loses}}; vs RF {{VS_RF}}.
Leak {{LEAK_AUC}}: {{LEAK_SENTENCE}}. Claims {{CLAIMS_VERIFIED}}/{{CLAIMS_N}}, {{CLAIMS_FLAGGED}} flagged.
Brier {{BRIER}} over {{BRIER_N}}; schema failures {{SCHEMA_FAILURES}}; changed {{N_CHANGED}} of {{A5_N}}.
In the featured run it {{WHAT_IT_DID}}; it moved to {{WHAT_IT_USED}}.
Decode {{TOKS}}, prefill {{PREFILL_TOKS}}, {{CAMPAIGN_MIN}} min, {{MACOS_VERSION}}, build {{LLAMA_BUILD}}.
DFlash {{DFLASH_RESULT}}. Swap {{SWAP_GB}} GB. Commits {{N_COMMITS}}, {{FINAL_SHA}}, runs {{RUNS_RANGE}}.
{{REPO_URL}} {{SITE_URL}} {{EVENT_HASHTAG}} {{NOT_A_PLACEHOLDER}}. Fill every `{{...}}` first.
"""
SUBMISSION = "Summary: {{HEADLINE_SHORT}}. {{DFLASH_SENTENCE}} Thread {{X_THREAD_URL}}.\n"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def sh(root, *args):
    return subprocess.run([*GIT, "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def bench_report(profile, draft, tps, accepted=None, drafted=None):
    cold = {"seconds": 100.0, "finish_reason": "length", "cache_n": 0, "predicted_n": 900,
            "predicted_per_second": tps, "prompt_n": 1685, "prompt_per_second": 33.4}
    if drafted:
        cold.update(draft_n=drafted, draft_n_accepted=accepted)
    cmd = "llama-server -m Q3.gguf --mmproj mmproj.gguf " + (f"-md {draft} " if draft else "") + "-c 32768"
    return {"kind": "bench", "ts": "20261029-214932", "profile": profile, "started": True, "quant": "UD-Q3_K_XL",
            "draft": draft, "llama_build": 11960, "server_cmd": cmd, "bench": {"cold": cold, "warm": cold}}


def write_mac_files(root):
    (root / "mac").mkdir()
    (root / "mac/profiles.json").write_text(json.dumps({
        "files": {"dflash": {"file": "dflash.gguf"}},
        "profiles": {"default": {"model": "q3", "mmproj": "mmproj", "draft": "dflash", "quant": "UD-Q3_K_XL"}}}))
    (root / "doctor").mkdir()
    reports = {
        "20261029-214932-bench-small.json": bench_report("small", "", 2.998),
        "20261029-214932-bench-small-dflash.json": bench_report("small-dflash", "dflash.gguf", 6.83, 603, 883),
        # A different quant with the drafter must not be picked for the default profile.
        "20261029-214932-bench-q4-dflash.json": {**bench_report("q4-dflash", "dflash.gguf", 7.01, 618, 840),
                                                 "quant": "Q4_K_M"},
        "20261029-225521.json": {"kind": "doctor", "ts": "20261029-225521", "mock": False, "profile": "default",
                                 "profile_spec": {"quant": "UD-Q3_K_XL"}, "llama_build": 11960,
                                 "machine": {"chip": "Apple M4", "ram_gb": 24, "os": "macOS 26.5.2"},
                                 "memory": {"free_pct_after": 25}},
        "20261030-010000-record-main.json": {"kind": "record", "ts": "20261030-010000", "mock": False,
                                             "memory": {"swap_max_growth_mb": 1946}},
        "20261030-020000-record-mock.json": {"kind": "record", "ts": "20261030-020000", "mock": True,
                                             "memory": {"swap_max_growth_mb": 9999}},
    }
    for name, rep in reports.items():
        (root / "doctor" / name).write_text(json.dumps(rep))
    (root / "web").mkdir()
    (root / "web/site.config.json").write_text(json.dumps({"REPO_URL": "https://example.org/repo",
                                                           "SITE_URL": "https://example.org/site/"}))


def ev(kind, role, payload, rnd=1, **kw):
    return {"t": "2026-10-30T12:00:00Z", "round": rnd, "role": role, "kind": kind, "payload": payload, **kw}


def llm(role, tps, prefill, prompt_n=1500):
    return ev("llm_call", role, {"model": "glimmer", "finish_reason": "stop"},
              timings={"prompt_n": prompt_n, "prompt_per_second": prefill, "predicted_n": 300,
                       "predicted_per_second": tps, "cache_n": 0}, dur_ms=50_000)


def query(sort_by, rnd=1):
    return ev("tool_call", "director", {"name": "query_pool", "args": {"sort_by": sort_by, "limit": 25}}, rnd)


def augment(run_dir, after_sort, schema_error):
    """Give a write_run ledger what a recorded campaign has: model and machine in the header, llm_call timings,
    the Director's ranking calls around the first calibration, and (optionally) a Proposer schema failure."""
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    out = []
    for e in events:
        if e["kind"] == "header":
            e["payload"].update(model={"name": "glimmer", "server_build": "b11960"},
                                machine={"chip": "Apple M4", "ram_gb": 24, "os": "macOS 26.5.2"})
            out += [e, llm("director", 5.0, 30.0), query("S_mace"), llm("proposer", 7.0, 40.0),
                    llm("proposer", 6.0, 999.0, prompt_n=50)]
            if schema_error:
                out += [ev("error", "proposer", {"where": "proposer.batch", "message": "bad", "recovered": True}),
                        ev("error", "system", {"where": "request_dft", "message": "budget", "recovered": False})]
            continue
        out.append(e)
        if e["kind"] == "calibration" and not any(x["kind"] == "calibration" for x in out[:-1]):
            out += [query(after_sort, e["round"])]
    for k, e in enumerate(out):
        e["seq"] = k
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in out))


def recorded_runs(root, data):
    """Recorded (non-mock) A4/A5/A6 runs on P1 and P2, two seeds, plus a leak probe. A5 on P1 leaves the cheap
    scores for pore size after its first calibration; A5 on P2 keeps ranking by S_mace."""
    rng = np.random.default_rng(3)
    hit_of = dict(zip(data.labels.id, data.labels.hit.astype(bool)))
    for arm, noise in (("A4", 1.0), ("A5", 0.3), ("A6", 0.5)):
        for pool in ("P1", "P2"):
            df = data.pool(pool)
            for s in range(2):
                score = df.S.to_numpy() + noise * rng.normal(size=len(df))
                order = np.argsort(score)[:BUDGET]
                picks = list(df.id.to_numpy()[order])
                p_hits = [float(np.clip(0.5 - 0.5 * score[k], 0, 1)) for k in order] if arm != "A4" else None
                rid = write_run(root, arm, pool, s, picks, hit_of, p_hits=p_hits, mock=False,
                                critiques=[(0.1, 0.05), (0.2, 0.3)] if arm != "A4" else (),
                                claims={"n_claims": 10, "verified": 9, "flagged": [{"claim": "x", "reason": "y"}]})
                augment(root / "runs" / rid, "pld_A" if (arm, pool) == ("A5", "P1") else "S_mace",
                        schema_error=arm == "A5" and s == 0)
    ranking = rng.permutation(data.pool("P1").id.to_numpy()).tolist()
    write_run(root, "L", "P1", 0, ranking[:BUDGET], hit_of, mock=False, extra={"ranking": ranking})


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(surrogate, "N_TREES", 20)
    root = tmp_path_factory.mktemp("filldocs")
    make_data(root)
    data = load_data(root / "data")
    (root / "results").mkdir()
    arms.run_all(data, TEST_POOLS, 2, log=lambda *_: None).to_parquet(root / "results/baselines.parquet")
    write_mac_files(root)
    (root / "README.md").write_text(DOC)
    (root / "SUBMISSION.md").write_text(SUBMISSION)
    sh(root, "init", "-q")
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "start")
    sh(root, "tag", "hackathon-start")
    (root / "PREREG.md").write_text("# prereg\n\nfrozen\r\n")
    sh(root, "add", "PREREG.md")
    sh(root, "commit", "-qm", "prereg")
    sh(root, "tag", "prereg-v1")
    recorded_runs(root, data)
    sh(root, "add", "runs")
    sh(root, "commit", "-qm", "runs")
    assert results.main(["--root", str(root)]) == 0
    yield root
    mp.undo()


@pytest.fixture
def repo(base, tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(base, dst)
    return dst


def values_of(root):
    return fill_docs.compute_values(fill_docs.Context(root))


def res_of(root):
    return json.loads((root / "results/results.json").read_text())


def test_compare_word_rule():
    word = fill_docs.compare_word
    assert word({"diff": 0.6, "ci": [0.1, 1.1], "p": 0.01}) == "beats"
    assert word({"diff": 0.6, "ci": [-0.1, 1.1], "p": 0.04}) == "beats"  # the test decides a win
    assert word({"diff": 0.3, "ci": [-0.2, 0.8], "p": 0.2}) == "ties"
    assert word({"diff": 0.3, "ci": [0.05, 0.8], "p": 0.06}) == "ties"  # CI above 0 but not significant
    assert word({"diff": 0.0, "ci": [-0.3, 0.3], "p": 0.01}) == "ties"  # p < 0.05 needs a positive diff
    assert word({"diff": -0.2, "ci": [-0.6, 0.1], "p": 0.9}) == "ties"
    assert word({"diff": -1.0, "ci": [-1.5, -0.5], "p": 0.99}) == "loses"


def test_values_come_from_results(base):
    res, v = res_of(base), values_of(base)
    arm = {a["arm"]: a for a in res["arms"]}
    test = {t["name"]: t for t in res["tests"]}
    assert res["status"] == "pilot"
    for a in ("A0", "A1", "A2", "A3", "A4", "A5", "A6"):
        assert v[f"{a}_N"].value == str(arm[a]["n_runs"])
        assert v[f"{a}_HITS"].value == f"{arm[a]['hits_mean']:.2f}"
        assert v[f"{a}_LO"].value == f"{arm[a]['hits_ci'][0]:.2f}"
        assert v[f"{a}_HI"].value == f"{arm[a]['hits_ci'][1]:.2f}"
        assert v[f"{a}_ENR"].value == f"{arm[a]['enrichment']:.2f}×"
        assert v[f"{a}_LPH"].value == f"{arm[a]['lookups_per_hit']:.1f}"
    primary = test["primary: A5 - A2 hits@40"]
    assert v["A5_A2_DIFF"].value == f"{primary['diff']:+.2f}"
    assert float(v["A5_A2_P"].value) == pytest.approx(primary["p"], abs=1e-3)
    for key, name in (("VS_RANDOM", "A5 - A0 hits@40"), ("VS_A2", "primary: A5 - A2 hits@40"),
                      ("VS_RF", "A5 - A3 hits@40")):
        assert v[key].value == fill_docs.compare_word(test[name])
    assert v["H"].value == v["A5_HITS"].value
    assert v["RANDOM_HITS"].value == f"{np.mean([c['random_expected'] for c in res['ceilings']]):.2f}"
    assert v["CLAIMS_N"].value == "120" and v["CLAIMS_VERIFIED"].value == "108" and v["CLAIMS_FLAGGED"].value == "12"
    assert v["BRIER_N"].value == str(res["calibration"]["n"])
    assert v["LEAK_AUC"].value == f"{res['leak']['L_auc']:.2f}"
    assert "ranking the raw refcodes alone gives AUC" in v["LEAK_SENTENCE"].value
    assert v["RESULTS_STATUS"].value == "pilot"


def test_headlines_name_random_and_strongest_baseline(base):
    v = values_of(base)
    for key in ("HEADLINE", "HEADLINE_SHORT"):
        text = v[key].value
        assert "random" in text and "MACE-greedy" in text and v["A5_HITS"].value in text
        assert v["A2_HITS"].value in text and v["A0_HITS"].value in text
    assert len(v["HEADLINE_SHORT"].value.split()) < 35
    assert v["VS_A2"].value in v["WIN_TIE_LOSS_SENTENCE"].value.replace("loses to", "loses")


def test_trace_rules(base):
    v = values_of(base)
    # A5 on P1 (2 runs) moved from S_mace to pld_A after the first calibration; A5 on P2 kept S_mace.
    assert v["N_CHANGED"].value == "2"
    assert v["WHAT_IT_USED"].value == "pore-limiting diameter (pld_A)"
    assert v["WHAT_IT_DID"].value == ("checked the cheap simulator against its first 10 DFT labels (Spearman +0.05), "
                                      "then switched from ranking by the cheap MACE score (S_mace) to pore-limiting "
                                      "diameter (pld_A)")
    assert "showcase run A5-P1-s" in v["WHAT_IT_DID"].source
    # Two A5 runs log one proposer.batch failure each; the request_dft error is not a schema failure.
    assert v["SCHEMA_FAILURES"].value == "2"


def test_showcase_rule_median_then_first_hit_then_seed():
    def run(seed, flags, pool="P1"):
        return SimpleNamespace(arm="A5", pool=pool, seed=seed, run_id=f"A5-{pool}-s{seed}", hit_flags=flags)
    one_late, one_early, three, zero = [False] * 30 + [True], [True], [True, True, True], []
    runs = [run(0, three), run(1, one_late), run(2, zero), run(3, one_early), run(4, one_late),
            run(5, [True] * 9, pool="P2")]
    assert fill_docs.pick_showcase(runs).seed == 3  # median 1 hit; earliest first hit wins
    assert fill_docs.pick_showcase([run(1, one_late), run(4, one_late)]).seed == 1  # then the lowest seed
    assert fill_docs.pick_showcase([run(5, three, pool="P2")]) is None


def test_hardware_git_and_site_values(base):
    v = values_of(base)
    assert v["TOKS"].value == "6.0"  # median of 5, 7, 6 per run
    assert v["PREFILL_TOKS"].value == "35"  # median of 30 and 40; the 50-token prompt (999 tok/s) is left out
    assert v["CAMPAIGN_MIN"].value == "1.0"  # synthetic campaigns last 60 s
    assert v["MACOS_VERSION"].value == "macOS 26.5.2" and v["LLAMA_BUILD"].value == "b11960"
    assert v["DFLASH_RESULT"].value == "3.0 → 6.8 tok/s with Meta's DFlash drafter, 68% of drafted tokens accepted"
    assert "bench-small.json vs doctor/20261029-214932-bench-small-dflash.json" in v["DFLASH_RESULT"].source
    assert "3.0 to 6.8 tok/s" in v["DFLASH_SENTENCE"].value
    assert v["SWAP_GB"].value == "1.9"  # the mock record report is ignored
    assert v["MEM_GB"].value == "18.0"
    prereg = (base / "PREREG.md").read_bytes()
    assert v["PREREG_SHA"].value == hashlib.sha256(prereg).hexdigest()
    assert v["N_COMMITS"].value == "2"
    assert v["FINAL_SHA"].value == sh(base, "rev-parse", "--short=7", "HEAD")
    assert v["RUNS_RANGE"].value == sh(base, "rev-parse", "--short=7", "HEAD")
    assert v["REPO_URL"].value == "https://example.org/repo" and v["SITE_URL"].value == "https://example.org/site/"


def test_hardware_falls_back_to_bench_without_runs(repo):
    shutil.rmtree(repo / "runs")
    v = values_of(repo)
    assert v["TOKS"].value == "6.8" and "bench-small-dflash.json" in v["TOKS"].source
    assert v["PREFILL_TOKS"].value == "33"
    assert v["CAMPAIGN_MIN"].value is None
    assert v["MACOS_VERSION"].value == "macOS 26.5.2" and v["LLAMA_BUILD"].value == "11960"


def test_check_lists_missing_human_and_unknown(base, capsys):
    rc = fill_docs.main(["--check", "--root", str(base)])
    out = capsys.readouterr().out
    assert rc == 1  # an unknown placeholder name is a failure
    assert "| A5_HITS | " in out and "| VIDEO_URL | MISSING | needs a human" in out
    assert "| NOT_A_PLACEHOLDER | UNKNOWN |" in out and "unknown placeholder names: NOT_A_PLACEHOLDER" in out
    assert "| ... |" not in out
    for name in ("VIDEO_URL", "TEAM_NAME", "X_THREAD_URL", "EVENT_HASHTAG"):
        assert name in out.split("needs a human: ")[-1]


def test_write_fills_values_and_leaves_human_ones(repo, capsys):
    before = (repo / "README.md").read_text()
    assert fill_docs.main(["--write", "--root", str(repo)]) == 0
    text = (repo / "README.md").read_text()
    v = values_of_written(repo)
    assert f"| A5 | {v['A5_N']['value']} | {v['A5_HITS']['value']} [" in text
    assert f"against MACE-greedy it {v['VS_A2']['value']};" in text and "beats / ties / loses" not in text
    for keep in ("{{VIDEO_URL}}", "{{TEAM_NAME}}", "{{EVENT_HASHTAG}}", "{{NOT_A_PLACEHOLDER}}", "`{{...}}`"):
        assert keep in text and keep in before
    assert "{{A5_HITS}}" not in text and "{{HEADLINE}}" not in text
    sub = (repo / "SUBMISSION.md").read_text()
    assert "{{HEADLINE_SHORT}}" not in sub and "{{X_THREAD_URL}}" in sub
    assert v["VIDEO_URL"] == {"value": None, "source": v["VIDEO_URL"]["source"], "human": True,
                              "files": ["README.md"]}
    assert v["A0_HITS"]["source"].startswith("results.json arms[A0 random]")


def values_of_written(root):
    return json.loads((root / "results/doc_values.json").read_text())["values"]


def test_write_refuses_mock_results(repo, capsys):
    shutil.rmtree(repo / "runs")
    data = load_data(repo / "data")
    make_mock_runs(repo, data)
    mp = pytest.MonkeyPatch()
    mp.setattr(surrogate, "N_TREES", 20)
    try:
        assert results.main(["--root", str(repo), "--include-mock"]) == 0
    finally:
        mp.undo()
    assert res_of(repo)["status"] == "mock"
    before = {f: (repo / f).read_text() for f in ("README.md", "SUBMISSION.md")}
    assert fill_docs.main(["--write", "--root", str(repo)]) == 2
    assert "status mock" in capsys.readouterr().err
    assert {f: (repo / f).read_text() for f in before} == before
    assert not (repo / "results/doc_values.json").exists()
    # Mock runs never count as recorded hardware measurements, even when --allow-mock lets the fill run.
    assert fill_docs.main(["--write", "--allow-mock", "--root", str(repo)]) == 0
    v = values_of_written(repo)
    assert v["RESULTS_STATUS"]["value"] == "mock"
    assert "bench-small-dflash.json" in v["TOKS"]["source"]
    assert "{{A5_HITS}}" not in (repo / "README.md").read_text()


def test_write_refuses_without_results(repo, capsys):
    (repo / "results/results.json").unlink()
    assert fill_docs.main(["--write", "--root", str(repo)]) == 2
    assert "not found" in capsys.readouterr().err
