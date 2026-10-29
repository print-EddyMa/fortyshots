"""Shared helpers for tests/test_core_*.py (agent core)."""
import json
from pathlib import Path

import httpx

from fortyshots import mockllm
from fortyshots.campaign import Campaign, RunConfig

FIXTURE_DATA = Path(__file__).parent / "data"
BASE_URL = "http://127.0.0.1:8080"
ENVELOPE = {"seq", "t", "run_id", "round", "role", "kind", "payload", "call_id", "tokens", "timings", "dur_ms"}
ROLES = {"director", "proposer", "critic", "reporter", "chemist", "system", "human"}
PAYLOAD_KEYS = {
    "header": {"arm", "pool", "seed", "budget", "rounds", "batch", "goal", "mode", "model", "machine", "network",
               "prereg_sha", "git_sha"},
    "llm_call": {"model", "reasoning_strength", "n_messages", "n_images", "finish_reason", "schema"},
    "reasoning": {"text"}, "message": {"text"}, "tool_call": {"name", "args"},
    "tool_result": {"name", "ok", "summary", "result", "image"},
    "proposal": {"picks"}, "critique": {"visual_rho", "vetoes", "notes"}, "human": {"action", "ids", "note"},
    "dft_order": {"ids", "rationale", "spent", "remaining"}, "dft_result": {"results", "hits_total", "spent"},
    "calibration": {"n", "spearman", "auc", "image"}, "mutation": {"parent", "spec", "status", "note"},
    "report": {"text"}, "claimcheck": {"n_claims", "verified", "flagged"}, "error": {"where", "message", "recovered"},
    "summary": {"arm", "pool", "seed", "hits", "spent", "picks", "hit_flags", "lookups_to_first_hit", "brier",
                "p_hits", "duration_s", "aborted", "autofill"},
}


class MockTransport(httpx.MockTransport):
    """In-process mock llama-server that records every chat request; `hook(req, resp)` may rewrite responses."""

    def __init__(self, hook=None, fail_chat: bool = False):
        self.requests: list[dict] = []
        self.hook = hook
        self.fail_chat = fail_chat
        inner = mockllm.transport()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/chat/completions":
                if self.fail_chat:
                    raise httpx.ConnectError("no network in this test")
                req = json.loads(request.content)
                self.requests.append(req)
                resp = mockllm.respond(req)
                if self.hook:
                    resp = self.hook(req, resp) or resp
                return httpx.Response(200, json=resp)
            return inner.handle_request(request)
        super().__init__(handler)


def make_cfg(tmp_path: Path, arm: str = "A5", pool: str = "P0", seed: int = 0, **kw) -> RunConfig:
    kw.setdefault("runs_dir", tmp_path / "runs")
    return RunConfig(arm=arm, pool=pool, seed=seed, mode=kw.pop("mode", "mock"), base_url=BASE_URL,
                     data_dir=FIXTURE_DATA, doctor_report=None, **kw)


def run(cfg: RunConfig, run_id: str = "mock-test-run", transport=None) -> tuple[dict, Campaign]:
    camp = Campaign(cfg, run_id=run_id, transport=transport or MockTransport())
    return camp.run(), camp


def events(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (Path(run_dir) / "events.jsonl").read_text().splitlines()]


def comparable(evs: list[dict]) -> list[str]:
    """Events without wall-clock fields, as canonical JSON lines."""
    out = []
    for e in evs:
        e = {k: v for k, v in e.items() if k not in ("t", "dur_ms")}
        if e["kind"] == "header":
            e["payload"] = {k: v for k, v in e["payload"].items() if k not in ("config", "network")}
        if e["kind"] == "summary":
            e["payload"] = {k: v for k, v in e["payload"].items() if k != "duration_s"}
        out.append(json.dumps(e, sort_keys=True))
    return out


def summary_core(s: dict) -> dict:
    return {k: v for k, v in s.items() if k != "duration_s"}
