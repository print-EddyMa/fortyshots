"""Round-trip checks against a llama-server (or the mock): chat, tools, json_schema, vision, reasoning budget,
decode speed, cold vs warm prefill per role prompt, and an estimated campaign duration."""
import io
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import attest
from .llm import LLMClient, LLMResult, image_part
from .roles import prompts as P
from .roles.schemas import Batch, batch_schema, review_schema

REQUIRED = ("server", "plain_chat", "tool_call", "tool_roundtrip", "json_schema", "vision", "reasoning_budget")
ADD_TOOL = [{"type": "function", "function": {
    "name": "add", "description": "Add two integers.",
    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                   "required": ["a", "b"]}}}]
MATH_SCHEMA = ("doctor_math", {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"],
                               "additionalProperties": False})
VISION_SCHEMA = ("doctor_vision", {"type": "object", "properties": {"count": {"type": "integer"}},
                                   "required": ["count"], "additionalProperties": False})
LONG_THINK = ("Think it through carefully before answering. A lab has 40 experiments to spend over 4 rounds on a pool "
              "of 500 candidates in which about 14 are hits. Compare spending 10 per round with spending 20, 10, 5, 5, "
              "and explain which is better for finding hits when early results can improve a ranking model. "
              "Answer in two sentences.")
DEFAULT_REASONING_BUDGET = 1024


def red_dots_png(n: int, seed: int = 7) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rng = random.Random(seed)
    fig, ax = plt.subplots(figsize=(5.12, 5.12), facecolor="white")
    ax.set_axis_off()
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    pts = []
    while len(pts) < n + 3:
        p = (rng.uniform(1, 9), rng.uniform(1, 9))
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > 4 for q in pts):
            pts.append(p)
    for k, (x, y) in enumerate(pts):
        ax.scatter(x, y, s=900, c="#e03131" if k < n else "#868e96", edgecolors="none")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue()


def _timing(res: LLMResult) -> dict:
    t = res.timings or {}
    return {"prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"), "prompt_ms": t.get("prompt_ms"),
            "prompt_per_second": t.get("prompt_per_second"), "predicted_n": t.get("predicted_n"),
            "predicted_per_second": t.get("predicted_per_second"), "finish_reason": res.finish_reason,
            "reasoning_chars": len(res.reasoning), "dur_ms": res.dur_ms}


class Doctor:
    def __init__(self, base_url: str, data_dir: Path, pool: str, transport=None):
        self.client = LLMClient(base_url, timeout=600, retries=1, transport=transport)
        self.data_dir = data_dir
        self.pool = pool
        self.checks: list[dict] = []
        self.reasoning_budget = DEFAULT_REASONING_BUDGET
        self.img_dir = Path("doctor/img")

    def check(self, name: str, fn):
        required = name in REQUIRED
        t0 = time.monotonic()
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 - every failure becomes a report line
            ok, detail = False, {"error": f"{type(e).__name__}: {e}"[:500]}
        self.checks.append({"name": name, "ok": bool(ok), "required": required, "detail": detail,
                            "dur_ms": int((time.monotonic() - t0) * 1000)})
        print(f"  {'ok  ' if ok else 'FAIL'} {name}", flush=True)
        return ok, detail

    def count_tokens(self, text: str) -> int:
        try:
            r = self.client.post("/tokenize", {"content": text}, timeout=30)
            tokens = r.json().get("tokens") if r.status_code == 200 else None
        except Exception:  # noqa: BLE001 - fall back to a character estimate
            tokens = None
        return len(tokens) if tokens is not None else len(text) // 4

    # ---- checks ---------------------------------------------------------------------------
    def server(self):
        props = self.client.get("/props").json()
        health = self.client.get("/health")
        models = self.client.get("/v1/models").json()
        flags = props.get("default_generation_settings") or {}
        self.props = props
        return health.status_code == 200, {
            "model_path": props.get("model_path"), "build_info": props.get("build_info"),
            "n_ctx": flags.get("n_ctx"), "total_slots": props.get("total_slots"),
            "modalities": props.get("modalities"), "models": [m.get("id") for m in models.get("data") or []]}

    def plain_chat(self):
        res = self.client.chat([{"role": "user", "content": "Reply with the single word READY."}],
                               reasoning_strength="low", max_tokens=1024)
        return "READY" in res.content.upper(), {"content": res.content[:80], **_timing(res)}

    def tool_call(self):
        res = self.client.chat([{"role": "user", "content": "Use the add tool for both sums, as two parallel calls: "
                                                            "17 + 25 and 3 + 4."}],
                               tools=ADD_TOOL, tool_choice="auto", reasoning_strength="low", max_tokens=1024)
        calls = [{"name": c.name, "args": c.arguments} for c in res.tool_calls]
        ok = any(c["name"] == "add" and c["args"] and {c["args"].get("a"), c["args"].get("b")} == {17, 25}
                 for c in calls)
        self.parallel = len(calls) >= 2
        return ok, {"tool_calls": calls, "parallel": self.parallel, **_timing(res)}

    def tool_roundtrip(self):
        """The Director's history format: assistant tool_calls, then tool messages, then a final answer."""
        msgs = [{"role": "user", "content": "Use the add tool to compute 17 + 25, then tell me the result."},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c0001", "type": "function", "function": {"name": "add",
                                                                     "arguments": json.dumps({"a": 17, "b": 25})}}]},
                {"role": "tool", "tool_call_id": "c0001", "content": "[c0001] add: 42"}]
        res = self.client.chat(msgs, tools=ADD_TOOL, tool_choice="auto", reasoning_strength="low", max_tokens=1024)
        return "42" in res.content, {"content": res.content[:120], "n_tool_calls": len(res.tool_calls),
                                     **_timing(res)}

    def parallel_tools(self):
        return self.parallel, {"n_calls_in_one_turn": 2 if self.parallel else "<2"}

    def json_schema(self, with_tools: bool = False):
        res = self.client.chat([{"role": "user", "content": "What is 17 * 23? Reply with JSON."}], schema=MATH_SCHEMA,
                               tools=ADD_TOOL if with_tools else None, tool_choice="none" if with_tools else None,
                               reasoning_strength="low", max_tokens=1024)
        ans = json.loads(res.content).get("answer")
        return ans == 391, {"content": res.content[:80], "answer": ans, **_timing(res)}

    def lab_schema(self):
        from .tools import PoolEnv
        env = PoolEnv(self.data_dir, self.pool, Path("/nonexistent"))
        ids = env.ids[:30]
        listing = "\n".join(ids)
        res = self.client.chat([{"role": "user", "content": f"[c0001] query_pool: listing\n{listing}\n"
                                                            "Pick 3 of these ids with p_hit values. JSON only."}],
                               schema=("Batch", batch_schema(3, ids, ["c0001"])), reasoning_strength="low",
                               max_tokens=2048)
        batch = Batch.model_validate_json(res.content)
        ok = len(batch.picks) == 3 and all(p.id in ids for p in batch.picks)
        return ok, {"picks": [p.id for p in batch.picks], **_timing(res)}

    def vision(self):
        png = red_dots_png(5)
        res = self.client.chat([{"role": "user", "content": [
            {"type": "text", "text": "How many red dots are in this image? Ignore the grey ones. Reply with JSON."},
            image_part(png)]}], schema=VISION_SCHEMA, reasoning_strength="low", max_tokens=1024)
        count = json.loads(res.content).get("count")
        return count == 5, {"count": count, "expected": 5, **_timing(res)}

    def vision_parity(self):
        from scipy.stats import spearmanr

        from .render import parity_plot
        rng = np.random.default_rng(3)
        x = rng.normal(0, 0.6, 30)
        y = 0.8 * x + rng.normal(0, 0.35, 30)
        rho = float(spearmanr(x, y).statistic)
        png = parity_plot(x.tolist(), y.tolist(), (y < -0.5).tolist())
        res = self.client.chat([{"role": "user", "content": [
            {"type": "text", "text": "Read this parity plot and estimate the rank correlation between the two axes, "
                                     "from -1 to 1. Veto nothing. Reply with JSON only."},
            image_part(png)]}], schema=("Review", review_schema(["m0000000"], [])), reasoning_strength="medium",
            max_tokens=2048)
        est = json.loads(res.content).get("visual_rank_correlation")
        err = None if est is None else round(abs(est - rho), 3)
        return err is not None and err < 0.3, {"true": round(rho, 3), "estimate": est, "abs_error": err,
                                               **_timing(res)}

    def reasoning_budget_check(self):
        out = {}
        for strength in ("low", "high"):
            res = self.client.chat([{"role": "user", "content": LONG_THINK}], reasoning_strength=strength,
                                   max_tokens=4096)
            out[strength] = {"reasoning_tokens": self.count_tokens(res.reasoning), "content_chars": len(res.content),
                             **_timing(res)}
        hi = out["high"]["reasoning_tokens"]
        ok = hi <= self.reasoning_budget * 1.1 + 32 and out["high"]["content_chars"] > 0
        return ok, {"budget": self.reasoning_budget, "within_budget": ok, **out}

    # ---- throughput per role ----------------------------------------------------------------
    def role_prompts(self) -> dict:
        from .tools import ARM_TOOLS, PoolEnv, tool_schemas
        img_dir = self.img_dir
        env = PoolEnv(self.data_dir, self.pool, img_dir, tools=ARM_TOOLS["A5"])
        sysmsg = {"role": "system", "content": P.system_prompt(
            goal="doctor probe", budget=40, rounds=4, batch=10, tools=env.tools, pool=P.pool_block(env.df),
            state=P.state_block(1, 4, 40, [], []))}
        director = [sysmsg, {"role": "user", "content": P.DIRECTOR.format(round=1, rounds=4, batch=10, max_calls=8,
                                                                         calibration="")}]
        q = env.query_pool(sort_by="S_mace", limit=25)
        proposer = director + [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c0002", "type": "function", "function": {
                "name": "query_pool", "arguments": json.dumps({"sort_by": "S_mace", "limit": 25})}}]},
            {"role": "tool", "tool_call_id": "c0002", "content": q.for_llm("c0002", "query_pool")},
            {"role": "user", "content": P.PROPOSER.format(round=1, rounds=4, n=10)}]
        rend = env.render_structure(id=q.ids[0], call_id="c0003")
        critic = [sysmsg, {"role": "user", "content": [
            {"type": "text", "text": P.CRITIC.format(round=1, rounds=4, n=1, batch=f"{q.ids[0]} 0.2 | probe | c0002",
                                                     evidence=P.CRITIC_NO_PARITY.format(render=""))},
            image_part((img_dir.parent / rend.image).read_bytes())]}]
        reporter = [sysmsg, {"role": "user", "content": P.REPORTER.format(evidence=q.for_llm("c0002", "query_pool"))}]
        return {
            "director": (director, {"tools": tool_schemas(env.tools), "tool_choice": "auto"}),
            "proposer": (proposer, {"schema": ("Batch", batch_schema(10, env.unbought(), ["c0002"]))}),
            "critic": (critic, {"schema": ("Review", review_schema([q.ids[0]], ["c0002"]))}),
            "reporter": (reporter, {}),
        }

    def throughput(self):
        roles = self.role_prompts()
        out = {}
        for role, (msgs, kw) in roles.items():
            nonce = f"[doctor run {time.time_ns()}]\n"
            cold_msgs = [{**msgs[0], "content": nonce + msgs[0]["content"]}] + msgs[1:]
            cold = self.client.chat(cold_msgs, reasoning_strength="low" if role == "reporter" else "medium",
                                    max_tokens=3072, **kw)
            warm = self.client.chat(cold_msgs, reasoning_strength="low" if role == "reporter" else "medium",
                                    max_tokens=16, **kw)
            out[role] = {"cold": _timing(cold), "warm": _timing(warm),
                         "completion_tokens": cold.completion_tokens, "prompt_tokens": cold.prompt_tokens}
        decode = [v["cold"]["predicted_per_second"] for v in out.values() if v["cold"]["predicted_per_second"]]
        self.tps = float(np.median(decode)) if decode else None
        ok = self.tps is not None and all(v["warm"]["cache_n"] is not None for v in out.values())
        return ok, {"decode_tok_s": self.tps, "roles": out}

    def estimate(self, thr: dict) -> dict:
        """Minutes per A5 campaign from measured per-role completion sizes, decode speed and prefill speeds."""
        roles = thr["roles"]
        tps = thr["decode_tok_s"] or 1.0

        def secs(role, cached: bool, calls: float) -> float:
            r = roles[role]
            prefill = (r["warm"] if cached else r["cold"])
            p_ms = (prefill["prompt_ms"] or 0) if not cached else (r["cold"]["prompt_ms"] or 0) * 0.15
            return calls * (p_ms / 1000 + r["completion_tokens"] / tps)
        per_round = (secs("director", False, 1) + secs("director", True, 4) + secs("proposer", False, 1.3)
                     + secs("critic", False, 1) + secs("proposer", True, 0.5))
        total = 4 * per_round + secs("reporter", False, 1)
        minutes = round(total / 60, 1)
        k = 5 if minutes <= 15 else 3 if minutes <= 25 else 2
        return {"campaign_minutes": minutes, "per_round_minutes": round(per_round / 60, 1), "seeds_per_pool_k": k,
                "rule": "<=15 min: k=5; <=25 min: k=3; else k=2 (PREREG)"}


def run_doctor(base_url: str, out: Path, data_dir: Path, pool: str = "P0", hash_gguf: str | None = None,
               transport=None) -> dict:
    d = Doctor(base_url, data_dir, pool, transport)
    d.img_dir = out.parent / "img"
    print(f"doctor: {base_url}", flush=True)
    _, server = d.check("server", d.server)
    d.check("plain_chat", d.plain_chat)
    d.check("tool_call", d.tool_call)
    d.check("parallel_tool_calls", d.parallel_tools)
    d.check("tool_roundtrip", d.tool_roundtrip)
    d.check("json_schema", d.json_schema)
    d.check("json_schema_with_tools", lambda: d.json_schema(with_tools=True))
    d.check("lab_batch_schema", d.lab_schema)
    d.check("vision", d.vision)
    d.check("vision_parity_reading", d.vision_parity)
    d.check("reasoning_budget", d.reasoning_budget_check)
    ok_thr, thr = d.check("throughput", d.throughput)
    model_path = (server or {}).get("model_path")
    model = {"gguf": Path(hash_gguf or model_path).name if (hash_gguf or model_path) else None,
             "sha256": attest.sha256_file(Path(hash_gguf)) if hash_gguf else os.environ.get("FORTYSHOTS_GGUF_SHA256"),
             "server_build": (server or {}).get("build_info"), "n_ctx": (server or {}).get("n_ctx")}
    report = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"), "base_url": base_url, "model": model,
        "machine": attest.machine_info(), "network": attest.network_state(), "checks": d.checks,
        "estimate": d.estimate(thr) if ok_thr else None,
        "recommendations": {
            "shared_tools": next((c["ok"] for c in d.checks if c["name"] == "json_schema_with_tools"), False),
            "action_mode": "native" if next((c["ok"] for c in d.checks if c["name"] == "tool_call"), False)
            else "schema",
            "vision": next((c["ok"] for c in d.checks if c["name"] == "vision"), False)},
    }
    report["ok"] = all(c["ok"] for c in d.checks if c["required"])
    report["summary"] = {c["name"]: c["ok"] for c in d.checks}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    return report


