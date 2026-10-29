import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import (
    ENVELOPE,
    PAYLOAD_KEYS,
    ROLES,
    MockTransport,
    comparable,
    events,
    make_cfg,
    run,
    summary_core,
)

from fortyshots.campaign import Campaign
from fortyshots.ledger import build_index


def _check_ledger(run_dir: Path, summary: dict):
    evs = events(run_dir)
    assert [e["seq"] for e in evs] == list(range(len(evs)))
    for e in evs:
        assert set(e) == ENVELOPE and e["role"] in ROLES and e["kind"] in PAYLOAD_KEYS, e
        assert PAYLOAD_KEYS[e["kind"]] <= set(e["payload"]), (e["kind"], set(e["payload"]))
        if e["kind"] == "llm_call":
            assert e["tokens"] and e["timings"] and e["call_id"]
        if e["kind"] in ("tool_call", "tool_result", "reasoning"):
            assert e["call_id"]
        if e["kind"] == "tool_result" and e["payload"]["image"]:
            assert (run_dir / e["payload"]["image"]).exists()
    assert evs[0]["kind"] == "header" and evs[-1]["kind"] == "summary"
    assert evs[-1]["payload"] == summary == json.loads((run_dir / "summary.json").read_text())
    return evs


@pytest.mark.parametrize("arm,kw", [("A5", {}), ("A6", {}), ("A4", {}), ("L2", {}), ("L", {}),
                                    ("A5", {"action_mode": "schema"}), ("A5", {"vision": False}),
                                    ("A6", {"action_mode": "schema", "vision": False})])
def test_every_arm_and_mode_completes(tmp_path, arm, kw):
    t0 = time.monotonic()
    summary, camp = run(make_cfg(tmp_path, arm, **kw))
    assert time.monotonic() - t0 < 60
    evs = _check_ledger(camp.run_dir, summary)
    kinds = {e["kind"] for e in evs}
    if arm in ("L", "L2"):
        assert summary["spent"] == 0 and len(summary["picks"]) == 40 and summary["leak"]["n_scored"] == 120
        return
    assert summary["spent"] == 40 and len(set(summary["picks"])) == 40 and not summary["aborted"]
    assert len(summary["hit_flags"]) == len(summary["p_hits"]) == 40
    assert {"proposal", "human", "dft_order", "dft_result", "report", "claimcheck"} <= kinds
    assert all(e["payload"]["action"] == "auto" for e in evs if e["kind"] == "human")
    calls = [e["payload"]["name"] for e in evs if e["kind"] == "tool_call"]
    if arm == "A6":
        assert "fit_surrogate" not in calls
    if arm == "A4":
        assert not {"fit_surrogate", "calibrate_oracle"} & set(calls) and "critique" not in kinds
        assert len([e for e in evs if e["kind"] == "dft_order"]) == 1
    if arm in ("A5", "A6"):
        assert "critique" in kinds and "calibration" in kinds
        crit = [e["payload"] for e in evs if e["kind"] == "critique"]
        assert crit[0]["visual_rho"] is None and all(c["vision"] == kw.get("vision", True) for c in crit)
    report = next(e for e in evs if e["kind"] == "report")
    assert report["role"] == "reporter" and report["payload"]["written_by"] == "muse-glimmer-30b-mock"
    cc = next(e for e in evs if e["kind"] == "claimcheck")["payload"]
    assert cc["n_claims"] > 0 and cc["flagged"] == []


def test_mandatory_calibration_is_enforced(tmp_path):
    _, camp = run(make_cfg(tmp_path, "A5"))
    evs = events(camp.run_dir)
    r3 = [e for e in evs if e["round"] == 3]
    assert any(e["kind"] == "message" and e["role"] == "system" and "mandatory" in e["payload"]["text"] for e in r3)
    assert any(e["kind"] == "tool_call" and e["role"] == "system" and e["payload"]["name"] == "calibrate_oracle"
               for e in r3)
    for r in (2, 3, 4):
        assert any(e["kind"] == "calibration" and e["round"] == r for e in evs)


class Crash(RuntimeError):
    pass


@pytest.mark.parametrize("crash_at", [5, 17, 27])
def test_resume_after_crash_matches_uninterrupted(tmp_path, crash_at):
    ref, ref_camp = run(make_cfg(tmp_path / "a", "A5"), run_id="mock-A5-P0-s0-202610300000")
    n_total = len(ref_camp.client.http._transport.requests)
    count = {"n": 0}

    def hook(req, resp):
        count["n"] += 1
        if count["n"] == crash_at:
            raise Crash("simulated kill")
    cfg = make_cfg(tmp_path / "b", "A5")
    with pytest.raises(Crash):
        run(cfg, run_id="mock-A5-P0-s0-202610300000", transport=MockTransport(hook=hook))
    run_dir = cfg.runs_dir / "mock-A5-P0-s0-202610300000"
    assert not (run_dir / "summary.json").exists()
    with open(run_dir / "events.jsonl", "a") as fh:
        fh.write('{"seq": 99999, "t": "torn wri')  # a write cut off by the kill
    t = MockTransport()
    summary, camp = run(cfg, run_id="mock-A5-P0-s0-202610300000", transport=t)
    assert camp.resumed and len(t.requests) == n_total - (crash_at - 1)  # earlier calls came from the cassette
    assert summary_core(summary) == summary_core(ref)
    assert comparable(events(run_dir)) == comparable(events(ref_camp.run_dir))


def test_cassette_replay_reproduces_the_run_byte_for_byte(tmp_path):
    ref, ref_camp = run(make_cfg(tmp_path / "a", "A5"), run_id="mock-A5-P0-s0-202610300001")
    cas = ref_camp.run_dir / "cassette.jsonl"
    cfg = make_cfg(tmp_path / "b", "A5", cassette="replay", cassette_from=cas)
    summary, camp = run(cfg, run_id="mock-A5-P0-s0-202610300001", transport=MockTransport(fail_chat=True))
    assert comparable(events(camp.run_dir)) == comparable(events(ref_camp.run_dir))
    assert summary_core(summary) == summary_core(ref)
    for png in (ref_camp.run_dir / "img").iterdir():
        assert (camp.run_dir / "img" / png.name).read_bytes() == png.read_bytes()
    assert not (camp.run_dir / "cassette.jsonl").exists()


@pytest.mark.parametrize("arm", ["A5", "A6"])
def test_time_cap_autofills_remaining_lookups(tmp_path, arm, monkeypatch):
    clock = {"t": 0.0}

    def fake_elapsed(self):
        clock["t"] += 1.0
        return clock["t"]
    monkeypatch.setattr(Campaign, "elapsed", fake_elapsed)
    summary, camp = run(make_cfg(tmp_path, arm, max_minutes=12 / 60))
    evs = events(camp.run_dir)
    assert summary["aborted"] and summary["spent"] == 40 and 0 < summary["autofill"] < 40
    assert any(e["kind"] == "error" and e["payload"]["where"] == "time_cap" for e in evs)
    order = [e for e in evs if e["kind"] == "dft_order"][-1]["payload"]
    assert order["rationale"].startswith("autofill")
    filled = summary["picks"][-summary["autofill"]:]
    assert all(p is None for p in summary["p_hits"][-summary["autofill"]:])
    if arm == "A6":
        bought_before = set(summary["picks"][: 40 - summary["autofill"]])
        expected = [i for i in camp.env.mace_ranking() if i not in bought_before][: summary["autofill"]]
        assert filled == expected
    report = next(e for e in evs if e["kind"] == "report")
    assert report["payload"]["source"] == "template"


def test_empty_length_cut_answer_is_retried_with_trimmed_context(tmp_path):
    state = {"done": False}

    def hook(req, resp):
        if not state["done"] and req.get("tools"):
            state["done"] = True
            msg = resp["choices"][0]
            msg["finish_reason"], msg["message"]["content"] = "length", ""
            msg["message"].pop("tool_calls", None)
        return resp
    summary, camp = run(make_cfg(tmp_path, "A5"), transport=MockTransport(hook=hook))
    errs = [e["payload"] for e in events(camp.run_dir) if e["kind"] == "error"]
    assert any(e["where"] == "director.llm" and e["recovered"] and "trimmed" in e["message"] for e in errs)
    assert summary["spent"] == 40


def test_invalid_proposer_output_falls_back_to_director_ranking(tmp_path):
    def hook(req, resp):
        if (req.get("response_format") or {}).get("json_schema", {}).get("name") == "Batch":
            resp["choices"][0]["message"]["content"] = "I think these look good."
        return resp
    summary, camp = run(make_cfg(tmp_path, "A5"), transport=MockTransport(hook=hook))
    evs = events(camp.run_dir)
    assert any(e["kind"] == "error" and e["payload"]["where"] == "proposer.fallback" for e in evs)
    assert summary["spent"] == 40 and summary["p_hits"][0] is None and summary["brier"] is None


def test_live_mode_waits_for_the_human_gate(tmp_path):
    cfg = make_cfg(tmp_path, "A5", mode="live", approval_poll_s=0.02)
    run_dir = cfg.runs_dir / "live-test"
    rejected = {}

    def approver():
        answered = set()
        while len(answered) < 4:
            p = run_dir / "pending.json"
            try:
                pend = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                time.sleep(0.01)
                continue
            r = pend["round"]
            if r in answered:
                time.sleep(0.01)
                continue
            entry = {"round": r, "action": "approve", "ids": [], "note": "looks fine"}
            if r == 2:
                entry = {"round": r, "action": "reject", "ids": pend["ids"][:2], "note": "too similar"}
                rejected["ids"] = pend["ids"][:2]
            with open(run_dir / "human.jsonl", "a") as fh:
                fh.write(json.dumps(entry) + "\n")
            answered.add(r)
    th = threading.Thread(target=approver, daemon=True)
    th.start()
    summary, _ = run(cfg, run_id="live-test")
    th.join(timeout=5)
    humans = [e["payload"] for e in events(run_dir) if e["kind"] == "human"]
    assert [h["action"] for h in humans] == ["approve", "reject", "approve", "approve"]
    assert summary["spent"] == 40 and not set(rejected["ids"]) & set(summary["picks"][10:20])
    assert not (run_dir / "pending.json").exists()


def test_index_lists_finished_runs(tmp_path):
    cfg = make_cfg(tmp_path, "A4")
    run(cfg, run_id="mock-A4-P0-s0-202610300002")
    (cfg.runs_dir / "unfinished").mkdir()
    rows = build_index(cfg.runs_dir)
    assert [r["run_id"] for r in rows] == ["mock-A4-P0-s0-202610300002"]
    assert set(rows[0]) == {"run_id", "arm", "pool", "seed", "mode", "hits", "spent", "duration_s", "started", "model"}
    assert json.loads((cfg.runs_dir / "index.json").read_text()) == rows
