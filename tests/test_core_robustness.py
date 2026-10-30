"""Regression tests for the reviewer's findings: model-server failures and resume, timeouts, context overflow,
harness-pick accounting, leak-probe scrubbing, prereg hashing, element symbols and the lab's llama.cpp build parse."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import BASE_URL, FIXTURE_DATA, MockTransport, events, make_cfg, run, summary_core

from fortyshots import attest, mockllm
from fortyshots.campaign import Campaign, RefcodeMap, trim_messages
from fortyshots.llm import ContextOverflow, LLMClient, LLMError, doctor_speeds
from fortyshots.render import SYMBOLS

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "mock-A5-P0-s0-202610300100"


class Kill(BaseException):
    """The process dies (nothing in the campaign may catch this)."""


def _schema(req: dict) -> str | None:
    return ((req.get("response_format") or {}).get("json_schema") or {}).get("name")


def _without_llm_errors(evs: list[dict]) -> list[str]:
    """Events minus model-call failures and seq numbers, for comparing a disturbed run with a clean one."""
    out = []
    for e in evs:
        if e["kind"] == "error" and e["payload"]["where"].endswith(".llm"):
            continue
        e = {k: v for k, v in e.items() if k not in ("t", "dur_ms", "seq")}
        if e["kind"] == "header":
            e["payload"] = {k: v for k, v in e["payload"].items() if k not in ("config", "network")}
        if e["kind"] == "summary":
            e["payload"] = {k: v for k, v in e["payload"].items() if k != "duration_s"}
        out.append(json.dumps(e, sort_keys=True))
    return out


def _proposer_timeout(nth: int, times: int = 1, after=None):
    """Hook: the nth Proposer (Batch) request and the next `times - 1` of them time out; then `after()` runs."""
    state = {"batch": 0, "failed": 0}

    def hook(req, resp):
        if _schema(req) == "Batch":
            state["batch"] += 1
            if state["batch"] >= nth and state["failed"] < times:
                state["failed"] += 1
                raise httpx.ReadTimeout("simulated read timeout")
        return resp
    return hook, state


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """One uninterrupted A5 run, the yardstick for the disturbed runs below."""
    summary, camp = run(make_cfg(tmp_path_factory.mktemp("ref"), "A5"), run_id=RUN_ID)
    return summary, events(camp.run_dir), len(camp.client.http._transport.requests)


# ---- 1. a model-server failure no longer bricks the run ------------------------------------------------------
def test_transient_timeout_is_retried_within_the_run(tmp_path, reference):
    ref, ref_evs, _ = reference
    hook, state = _proposer_timeout(2)
    summary, camp = run(make_cfg(tmp_path, "A5"), run_id=RUN_ID, transport=MockTransport(hook=hook))
    assert state["failed"] == 1 and summary_core(summary) == summary_core(ref)
    evs = events(camp.run_dir)
    errs = [e for e in evs if e["kind"] == "error" and e["payload"]["where"] == "proposer.llm"]
    assert len(errs) == 1 and errs[0]["payload"]["recovered"] and errs[0]["payload"]["cause"] == "transport"
    assert "timeout" in errs[0]["payload"]["message"] and "attempt 1 of 3" in errs[0]["payload"]["message"]
    nxt = evs[errs[0]["seq"] + 1]  # the same call, sent again
    assert nxt["kind"] == "llm_call" and nxt["call_id"] == errs[0]["call_id"]
    assert _without_llm_errors(evs) == _without_llm_errors(ref_evs)


def test_timeout_then_crash_then_three_resumes_matches_uninterrupted(tmp_path, reference):
    """The 2nd Proposer call times out and the process dies during the backoff; three resumes (two of them killed
    again) finish the run with the same summary as an uninterrupted one."""
    ref, ref_evs, n_ref = reference
    cfg = make_cfg(tmp_path, "A5")
    run_dir = cfg.runs_dir / RUN_ID
    hook, _ = _proposer_timeout(2)

    def health_kill():
        raise Kill("killed while waiting for the server")
    with pytest.raises(Kill):
        run(cfg, run_id=RUN_ID, transport=MockTransport(hook=hook, health=health_kill))
    evs = events(run_dir)
    assert evs[-1]["kind"] == "error" and evs[-1]["payload"]["where"] == "proposer.llm"
    assert not (run_dir / "summary.json").exists()

    sent = []
    for kill_at in (3, 4, None):  # resumes 1 and 2 die again a few calls in; resume 3 finishes
        count = {"n": 0}

        def hook2(req, resp, kill_at=kill_at, count=count):
            count["n"] += 1
            if kill_at is not None and count["n"] == kill_at:
                raise Kill("killed again")
            return resp
        t = MockTransport(hook=hook2)
        if kill_at is not None:
            with pytest.raises(Kill):
                run(cfg, run_id=RUN_ID, transport=t)
            assert not (run_dir / "summary.json").exists()
        else:
            summary, camp = run(cfg, run_id=RUN_ID, transport=t)
        sent.append(len(t.requests))
    assert camp.resumed and summary_core(summary) == summary_core(ref)
    assert json.loads((run_dir / "summary.json").read_text()) == summary
    assert _without_llm_errors(events(run_dir)) == _without_llm_errors(ref_evs)
    assert sent[-1] < n_ref  # earlier calls came back from the cassette


def test_legacy_trailing_unrecovered_error_is_not_binding(tmp_path, reference):
    """A ledger written by the old code (error event with recovered=false, then the crash) resumes cleanly."""
    ref, ref_evs, _ = reference
    cfg = make_cfg(tmp_path, "A5")
    state = {"batch": 0}

    def hook(req, resp):
        if _schema(req) == "Batch":
            state["batch"] += 1
            if state["batch"] == 2:
                raise Kill("the old code re-raised the LLMError here and the run died")
        return resp
    camp = Campaign(cfg, run_id=RUN_ID, transport=MockTransport(hook=hook))
    with pytest.raises(Kill):
        camp.run()
    evs = events(camp.run_dir)
    legacy = {"seq": len(evs), "t": evs[-1]["t"], "run_id": RUN_ID, "round": evs[-1]["round"], "role": "system",
              "kind": "error", "payload": {"where": "proposer.llm", "message": "timeout after 300s on "
                                           "/v1/chat/completions", "recovered": False},
              "call_id": f"c{camp._cid:04d}", "tokens": None, "timings": None, "dur_ms": None}
    with open(camp.run_dir / "events.jsonl", "a") as fh:
        fh.write(json.dumps(legacy) + "\n")
    summary, camp2 = run(cfg, run_id=RUN_ID)
    assert camp2.resumed and summary_core(summary) == summary_core(ref)
    assert _without_llm_errors(events(camp2.run_dir)) == _without_llm_errors(ref_evs)


def test_model_down_aborts_with_autofill_and_a_summary(tmp_path):
    state = {"batch": 0, "down": False, "sent_while_down": 0}

    def hook(req, resp):
        if _schema(req) == "Batch":
            state["batch"] += 1
            state["down"] = state["down"] or state["batch"] == 2
        if state["down"]:
            state["sent_while_down"] += 1
            return httpx.Response(500, json={"error": {"code": 500, "message": "slot crashed"}})
        return resp
    t = MockTransport(hook=hook, health=lambda: not state["down"])
    summary, camp = run(make_cfg(tmp_path, "A5"), transport=t)
    evs = events(camp.run_dir)
    assert summary["aborted"] and summary["spent"] == 40 and summary["autofill"] > 0
    assert json.loads((camp.run_dir / "summary.json").read_text()) == summary
    attempts = [e["payload"] for e in evs if e["kind"] == "error" and e["payload"]["where"] == "proposer.llm"]
    assert [a["attempt"] for a in attempts] == [1, 2, 3] and [a["recovered"] for a in attempts] == [True, True, False]
    assert any(e["kind"] == "error" and e["payload"]["where"] == "llm_unavailable" for e in evs)
    filled = summary["picks"][-summary["autofill"]:]
    bought_before = set(summary["picks"][: 40 - summary["autofill"]])
    assert filled == [i for i in camp.env.mace_ranking() if i not in bought_before][: summary["autofill"]]
    assert summary["harness_pick_ids"][-summary["autofill"]:] == filled
    assert next(e for e in evs if e["kind"] == "report")["payload"]["source"] == "template"
    assert state["sent_while_down"] == 3  # nothing was sent after the third failure


# ---- 2. per-call timeouts scale with max_tokens and the measured decode speed -----------------------------------
def test_timeout_scales_with_max_tokens_and_doctor_speed(tmp_path, monkeypatch):
    monkeypatch.delenv("FORTYSHOTS_TIMEOUT_S", raising=False)
    client = LLMClient(BASE_URL)
    small = client.build_request([{"role": "user", "content": "x"}], max_tokens=512)
    a4 = client.build_request([{"role": "user", "content": "x"}], max_tokens=5536)
    assert client.timeout_for(small) == 300  # the floor
    assert client.timeout_for(a4) >= 5536 / 5 * 1.5  # default 5 tok/s when there is no doctor report
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps({"checks": [{"name": "throughput", "ok": True, "detail": {
        "decode_tok_s": 12.0, "roles": {"director": {"cold": {"prompt_per_second": 400.0}}}}}]}))
    speeds = doctor_speeds(rep)
    assert speeds == {"decode_tps": 12.0, "prefill_tps": 400.0} and doctor_speeds(tmp_path / "none.json") == {
        "decode_tps": None, "prefill_tps": None}
    fast = LLMClient(BASE_URL, **speeds)
    assert 5536 / 12 * 1.5 <= fast.timeout_for(a4) < client.timeout_for(a4)
    monkeypatch.setenv("FORTYSHOTS_TIMEOUT_S", "42")
    assert LLMClient(BASE_URL).timeout_for(a4) == 42


def test_timeout_reaches_the_http_request_and_the_campaign_reads_the_doctor_report(tmp_path, monkeypatch):
    monkeypatch.delenv("FORTYSHOTS_TIMEOUT_S", raising=False)
    seen = []
    inner = MockTransport()

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return inner.handle_request(request)
    client = LLMClient(BASE_URL, transport=httpx.MockTransport(handler))
    client.chat([{"role": "user", "content": "Reply with the single word READY."}], max_tokens=8000)
    assert seen[-1] == pytest.approx(client.timeout_for(client.build_request(
        [{"role": "user", "content": "Reply with the single word READY."}], max_tokens=8000)))
    assert seen[-1] >= 8000 / 8 * 1.5
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps({"checks": [{"name": "throughput", "detail": {"decode_tok_s": 11.5, "roles": {}}}]}))
    cfg = make_cfg(tmp_path, "A4")
    cfg.doctor_report = rep
    camp = Campaign(cfg, run_id="t", transport=MockTransport())
    assert camp.client.decode_tps == 11.5


def test_a4_rationales_are_capped(tmp_path):
    t = MockTransport()
    run(make_cfg(tmp_path, "A4"), transport=t)
    batch = [r for r in t.requests if _schema(r) == "Batch"]
    assert batch
    for r in batch:
        pick = r["response_format"]["json_schema"]["schema"]["properties"]["picks"]["items"]["properties"]
        assert pick["rationale"]["maxLength"] <= 140
        assert "20 words or fewer" in r["messages"][-1]["content"]


# ---- 3. context overflow and length cuts ------------------------------------------------------------------------
def test_client_raises_context_overflow_only_for_context_errors():
    client = LLMClient(BASE_URL, transport=MockTransport(n_ctx=10))
    with pytest.raises(ContextOverflow):
        client.chat([{"role": "user", "content": "a long prompt " * 20}])

    def bad(req, resp):
        return httpx.Response(400, json={"error": {"code": 400, "message": "invalid grammar"}})
    client = LLMClient(BASE_URL, transport=MockTransport(hook=bad))
    with pytest.raises(LLMError) as e:
        client.chat([{"role": "user", "content": "x"}])
    assert not isinstance(e.value, ContextOverflow)


def test_trim_messages_drops_images_and_old_tool_results():
    img = mockllm.base64.b64encode(b"png").decode()
    msgs = [{"role": "system", "content": "s"}, {"role": "tool", "tool_call_id": "c1", "content": "line1\nline2"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "text", "text": "look"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}]}]
    kept = trim_messages(msgs)
    assert isinstance(kept[-1]["content"], list) and kept[1]["content"].startswith("line1 (details trimmed)")
    dropped = trim_messages(msgs, drop_images=True)
    assert isinstance(dropped[-1]["content"], str) and "images omitted" in dropped[-1]["content"]


def test_context_overflow_is_trimmed_and_retried(tmp_path):
    probe = MockTransport()
    run(make_cfg(tmp_path / "probe", "A5"), transport=probe)
    sizes = sorted(mockllm.prompt_tokens(r) for r in probe.requests)
    n_ctx = sizes[int(len(sizes) * 0.8)]  # the largest fifth of the prompts overflow
    t = MockTransport(n_ctx=n_ctx)
    summary, camp = run(make_cfg(tmp_path / "run", "A5"), transport=t)
    errs = [e["payload"] for e in events(camp.run_dir) if e["kind"] == "error" and e["payload"].get("cause") == "context"]
    assert any(e["recovered"] and "trimmed" in e["message"] for e in errs)
    assert summary["spent"] == 40 and not summary["aborted"]


def test_context_that_never_fits_degrades_to_empty_answers(tmp_path):
    summary, camp = run(make_cfg(tmp_path, "A5"), transport=MockTransport(n_ctx=10))
    evs = events(camp.run_dir)
    errs = [e["payload"] for e in evs if e["kind"] == "error" and e["payload"].get("cause") == "context"]
    assert any(not e["recovered"] and "empty answer" in e["message"] for e in errs)
    assert summary["spent"] == 40 and not summary["aborted"] and summary["autofill"] == 0
    assert summary["harness_picks"] == 40  # every pick was the harness's fallback, and the summary says so
    assert next(e for e in evs if e["kind"] == "report")["payload"]["source"] == "template"


def test_length_cut_retry_lowers_reasoning_strength(tmp_path):
    t = MockTransport(reasoning_tokens={"medium": 6000})  # every medium think overruns max_tokens
    from fortyshots.campaign import ROLE_STRENGTH

    cfg = make_cfg(tmp_path, "A5", strengths={**ROLE_STRENGTH, "proposer": "medium"})
    summary, camp = run(cfg, transport=t)
    evs = events(camp.run_dir)
    errs = [e["payload"] for e in evs if e["kind"] == "error" and e["payload"].get("cause") == "length"]
    assert errs and all(e["recovered"] and "reasoning_strength low" in e["message"] for e in errs)
    strengths = [r["chat_template_kwargs"]["reasoning_strength"] for r in t.requests]
    assert "low" in strengths and "medium" in strengths
    assert any(e["kind"] == "llm_call" and e["payload"]["reasoning_strength"] == "low"
               and e["role"] == "proposer" for e in evs)
    assert summary["spent"] == 40 and not summary["aborted"]


def test_mock_server_honours_the_env_hooks(monkeypatch):
    monkeypatch.setenv("FORTYSHOTS_MOCK_N_CTX", "5")
    client = LLMClient(BASE_URL, transport=mockllm.transport())
    with pytest.raises(ContextOverflow):
        client.chat([{"role": "user", "content": "this prompt is longer than five tokens"}])


# ---- 5. leak probe scrubbing --------------------------------------------------------------------------------------
@pytest.fixture
def id_map_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    refs = ["ABAVIJ", "ABAVIJ01", "NETHOJ_clean_h", "cm503311x_alf40K", "ZEDQEG"]
    pd.DataFrame({"id": [f"m{k:07x}" for k in range(1, len(refs) + 1)], "refcode": refs}).to_parquet(
        d / "id_map.parquet")
    return d


def test_refcode_scrubbing_is_case_insensitive_and_maps_suffixes(id_map_dir):
    rm = RefcodeMap(id_map_dir)
    text = ("abavij and ABAVIJ01 and Abavij07, NETHOJ_clean_h, cm503311x_alf40k, zedqeg02; "
            "unknown QWERTY and QWERTY12; keep CO2, H2O, DFT, m0000003 and lowercase words.")
    out = rm.scrub(text)
    assert out.startswith("m0000001 and m0000002 and m0000001, m0000003, m0000004, m0000005;")
    assert "[refcode] and [refcode]" in out and "CO2, H2O, DFT, m0000003" in out
    assert not re.search(r"abavij|qwerty|nethoj|zedqeg|cm503311", out, re.IGNORECASE)
    assert rm.resolve("abavij01") == "m0000002" and rm.resolve("ABAVIJ09") == "m0000001"
    assert rm.resolve("QWERTY") is None and rm.unscrub("m0000002") == "ABAVIJ01"


def test_leak_probe_scrubs_lowercase_suffixed_and_unknown_refcodes_and_errors(tmp_path):
    refs = pd.read_parquet(FIXTURE_DATA / "id_map.parquet")["refcode"].tolist()
    state = {"n": 0}

    def hook(req, resp):
        state["n"] += 1
        if state["n"] == 1:  # a server error that echoes a refcode: logged scrubbed, then retried
            return httpx.Response(500, json={"error": {"message": f"slot failed on {refs[0].lower()}"}})
        msg = resp["choices"][0]["message"]
        msg["reasoning_content"] += (f" I recall {refs[0].lower()}, {refs[1]}01 and {refs[2].title()}, "
                                     "and also XQZVWY.")
        body = json.loads(msg["content"])
        for sc in body["scores"]:
            sc["key"] = sc["key"].lower()  # lowercase keys are still matched to their MOFs
        msg["content"] = json.dumps(body)
        return resp
    summary, camp = run(make_cfg(tmp_path, "L"), run_id="mock-L-P0-s0-202610300200", transport=MockTransport(hook=hook))
    on_disk = "".join(p.read_text() for p in camp.run_dir.rglob("*") if p.is_file() and p.suffix != ".png")
    leaked = [r for r in refs if re.search(rf"(?<![A-Za-z0-9]){r}(\d{{2}})?(?![A-Za-z0-9])", on_disk, re.IGNORECASE)]
    assert not leaked and "XQZVWY" not in on_disk and "[refcode]" in on_disk
    errs = [e for e in events(camp.run_dir) if e["kind"] == "error" and e["payload"].get("cause") == "transport"]
    assert errs and re.search(r"m[0-9a-f]{7}", errs[0]["payload"]["message"])
    assert summary["leak"]["n_scored"] == 120
    assert not any(e["kind"] == "error" and "unscored" in e["payload"]["message"] for e in events(camp.run_dir))


# ---- 6, 7, 8 -------------------------------------------------------------------------------------------------------
def test_prereg_sha_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert attest.prereg_sha() == hashlib.sha256((ROOT / "PREREG.md").read_bytes()).hexdigest()


def test_symbols_cover_the_periodic_table_to_lawrencium():
    assert len(SYMBOLS) == 103 and min(SYMBOLS) == 1
    assert (SYMBOLS[1], SYMBOLS[26], SYMBOLS[32], SYMBOLS[57], SYMBOLS[71], SYMBOLS[92], SYMBOLS[103]) == (
        "H", "Fe", "Ge", "La", "Lu", "U", "Lr")


@pytest.mark.parametrize("version,build", [
    ("version: 11960 (abc1234)\nbuilt with Apple clang", "11960"),
    ("b11960", "11960"),
    ("llama-server b6010 (a1b2c3d)", "6010"),
    ("ggml_metal_init: found device\nversion: 5124 (bc091a4d)", "5124"),
    ("version: 0.8.0 (abc)", ""),
    ("version: 0.8.0 (b1234ef)", ""),
])
def test_lab_parses_the_llama_build(tmp_path, version, build):
    body = re.search(r"^llama_build\(\) \{\n.*?^\}\n", (ROOT / "lab").read_text(), re.MULTILINE | re.DOTALL).group(0)
    fake = tmp_path / "bin" / "llama-server"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\ncat >&2 <<'EOF'\n" + version + "\nEOF\n")
    fake.chmod(0o755)
    out = subprocess.run(["bash", "-c", body + "\nllama_build"], capture_output=True, text=True, check=True,
                         env={"PATH": f"{fake.parent}:/usr/bin:/bin"})
    assert out.stdout.strip() == build


def test_lab_warns_but_accepts_an_unknown_build(tmp_path):
    lab = (ROOT / "lab").read_text()
    # llama_build, llama_version_line and check_llama_build, as one block
    funcs = re.search(r"^llama_build\(\) \{\n.*?^check_llama_build\(\) \{\n.*?^\}\n", lab,
                      re.MULTILINE | re.DOTALL).group(0)
    fake = tmp_path / "bin" / "llama-server"
    fake.parent.mkdir()
    script = ("MIN_LLAMA_BUILD=10353\nwarn() { echo \"warn $*\"; }\ndie() { echo \"die $*\"; exit 3; }\n" + funcs
              + "check_llama_build && echo \"accepted ${LLAMA_BUILD:-unknown}\"\n")
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin"}
    cases = (("version: 0.8.0 (abc)", 0, ["warn Could not read", "accepted unknown"]),
             ("version: 12000 (x)", 0, ["accepted 12000"]), ("version: 9000 (x)", 3, ["die", "too old"]))
    for version, rc, expect in cases:
        fake.write_text(f"#!/bin/sh\necho '{version}' >&2\n")
        fake.chmod(0o755)
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=False)
        assert out.returncode == rc and all(x in out.stdout for x in expect), (version, out.stdout, out.stderr)


# ---- shared tool list rejected by the server: resend without tools, keep going ---------------------------------------
def test_rejected_shared_tools_fall_back_without_aborting(tmp_path):
    """If llama-server refuses tools together with a schema (or images) on a non-Director call, the call is resent
    without tools, the rest of the run stops sending them, and the campaign still finishes with every lookup."""
    def hook(req, resp):
        has_schema = (req.get("response_format") or {}).get("type") == "json_schema"
        if req.get("tools") and has_schema:
            return httpx.Response(400, json={"error": {"code": 400, "message": "tools with response_format not supported"}})
        return None

    summary, camp = run(make_cfg(tmp_path, "A5"), run_id=RUN_ID, transport=MockTransport(hook=hook))
    evs = events(camp.run_dir)
    rejected = [e for e in evs if e["kind"] == "error" and e["payload"].get("cause") == "tools_rejected"]
    assert len(rejected) == 1 and rejected[0]["payload"]["recovered"]
    assert summary["spent"] == 40 and not summary["aborted"] and summary["harness_picks"] == 0
    assert camp.cfg.shared_tools is False
