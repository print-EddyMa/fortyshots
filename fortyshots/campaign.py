"""One campaign: the round loop for arms A4/A5/A6 and the leak probes L/L2.

Resume works by re-running the campaign from the start: model calls come back from the cassette, tools are
deterministic, and the ledger matches re-emitted events against the events it already holds instead of writing them
again. Once the ledger is caught up, the run continues live.

Model-server failures (timeout, unreachable, HTTP error) are logged as `error` events carrying the call id and retried
with backoff and a /health check. A resumed run replays those events as failed attempts of the same call, so a call
that failed before a crash is simply sent again. After `llm_attempts` consecutive failures of one call, or at the time
cap, the campaign stops, autofills the rest of the budget and still writes its summary (`aborted: true`).
"""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import attest, claimcheck
from . import chemist as chem
from .cassette import Cassette
from .config import BATCH, BUDGET, DEFAULT_BASE_URL, DEFAULT_MAX_MINUTES, GOAL, MAX_DIRECTOR_CALLS, ROUNDS
from .data import load_id_map
from .ledger import Ledger
from .llm import ContextOverflow, LLMClient, LLMError, LLMResult, RequestRejected, doctor_speeds, image_part
from .roles import prompts as P
from .roles.schemas import (
    Batch,
    Mutations,
    Review,
    Scores,
    Steps,
    ValidationError,
    batch_schema,
    mutations_schema,
    review_schema,
    scores_schema,
    steps_schema,
)
from .tools import ARM_TOOLS, PoolEnv, ToolOutput, score_probe, tool_schemas

STRENGTHS = ("low", "medium", "high", "xhigh")
# Tuned to the base M4 (doctor, Oct 30): generation, not prefill, dominates; "low" cuts reasoning ~7x vs "high".
ROLE_STRENGTH = {"director": "low", "proposer": "medium", "critic": "low", "reporter": "low", "chemist": "medium",
                 "probe": "low"}
MAX_TOKENS = {"director": 2048, "proposer": 3072, "critic": 2048, "reporter": 2048, "chemist": 2048, "probe": 3072}
MAX_DIRECTOR_TURNS = 8
MAX_DIRECTOR_IMAGES = 2
PROBE_CHUNK = 50
RESULT_LEDGER_CHARS = 20000
A4_RATIONALE_CHARS = 140  # about 20 words: keeps the 40-pick A4 batch near 2.5K output tokens
MAX_BACKOFF_S = 120.0
REFCODE_LIKE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{6}(?:\d{2})?(?![A-Za-z0-9])")


class CampaignStop(Exception):
    """Stops the campaign early. The harness autofills the rest of the budget and the run counts (PREREG section 4)."""


class ToolsRejected(Exception):
    """The server refused a non-Director request that carried the shared tool list; resend it without tools."""


class TimeUp(CampaignStop):
    pass


class ModelDown(CampaignStop):
    """One model call failed `llm_attempts` times in a row."""


def lower_strength(strength: str) -> str:
    k = STRENGTHS.index(strength) if strength in STRENGTHS else 1
    return STRENGTHS[max(0, k - 1)]


@dataclass
class RunConfig:
    arm: str
    pool: str
    seed: int = 0
    mode: str = "mock"
    action_mode: str = "native"
    base_url: str = DEFAULT_BASE_URL
    max_minutes: float = DEFAULT_MAX_MINUTES
    runs_dir: Path = Path("runs")
    data_dir: Path = Path("data")
    vision: bool = True
    shared_tools: bool = True  # same tool list on every call keeps the prompt prefix cached (pilot: ~35K re-read tokens)
    cassette: str = "record"
    cassette_from: Path | None = None
    chemist: bool = False
    budget: int = BUDGET
    rounds: int = ROUNDS
    batch: int = BATCH
    max_calls: int = MAX_DIRECTOR_CALLS
    strengths: dict = field(default_factory=lambda: dict(ROLE_STRENGTH))
    doctor_report: Path | None = Path("doctor/report.json")
    approval_poll_s: float = 1.0
    llm_attempts: int = 3            # consecutive failures of one model call before the campaign aborts
    llm_backoff_s: float = 10.0      # first backoff after a failed call; doubles per attempt (capped at 120 s)
    llm_health_wait_s: float = 300.0  # how long to wait for /health to answer before the next attempt


def run_prefix(cfg: RunConfig) -> str:
    return f"{'mock-' if cfg.mode == 'mock' else ''}{cfg.arm}-{cfg.pool}-s{cfg.seed}-"


def new_run_id(cfg: RunConfig) -> str:
    return run_prefix(cfg) + datetime.now(UTC).strftime("%Y%m%d%H%M")


def find_runs(cfg: RunConfig) -> tuple[list[Path], list[Path]]:
    """(finished, unfinished) run dirs for this arm/pool/seed/mode, oldest first."""
    root = Path(cfg.runs_dir)
    pat = re.compile(re.escape(run_prefix(cfg)) + r"\d{12}$")
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and pat.match(d.name)) if root.exists() else []
    finished = [d for d in dirs if (d / "summary.json").exists()]
    unfinished = [d for d in dirs if not (d / "summary.json").exists() and (d / "events.jsonl").exists()]
    return finished, unfinished


class RefcodeMap:
    """Refcode <-> id rewriting for the leak probe L, whose prompts carry raw CSD refcodes.

    scrub() runs on everything the model wrote before it reaches the disk: refcodes match in any case and with up to
    two trailing digits (CSD suffixes like ABAVIJ01; an exact entry wins, otherwise the base refcode is used), and
    any other refcode-like token (six capitals, optionally two digits) becomes "[refcode]"."""

    def __init__(self, data_dir: Path):
        m = load_id_map(data_dir)
        self.ref2id = dict(zip(m["refcode"].astype(str), m["id"].astype(str)))
        self.id2ref = {v: k for k, v in self.ref2id.items()}
        self.fold: dict[str, str] = {}
        for ref, i in self.ref2id.items():
            self.fold.setdefault(ref.casefold(), i)
        alts = "|".join(map(re.escape, sorted(self.ref2id, key=len, reverse=True)))
        self.to_id = re.compile(r"(?<![A-Za-z0-9])(" + alts + r")(\d{2})?(?![A-Za-z0-9])", re.IGNORECASE)
        self.to_ref = re.compile(r"\b(m[0-9a-f]{7})\b")

    def resolve(self, token: str) -> str | None:
        """The id a refcode as the model wrote it stands for (case-insensitive, CSD suffix allowed), else None."""
        m = self.to_id.fullmatch(token.strip())
        return self.fold.get(m.group(1).casefold()) if m else None

    def scrub(self, text: str) -> str:
        text = self.to_id.sub(lambda x: self.fold[x.group(1).casefold()], text)
        return REFCODE_LIKE.sub("[refcode]", text)

    def unscrub(self, text: str) -> str:
        return self.to_ref.sub(lambda x: self.id2ref.get(x.group(1), x.group(1)), text)


def refcode_scrubbers(data_dir: Path):
    """(refcode -> id, id -> refcode) text rewriters for the leak probe."""
    m = RefcodeMap(data_dir)
    return m.scrub, m.unscrub


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return t


def trim_messages(messages: list[dict], drop_images: bool = False) -> list[dict]:
    """Shrinks a thread after an empty, length-cut answer or a context overflow: older tool results keep only their
    first line and only the last message keeps its images (none at all with drop_images)."""
    out, last = [], len(messages) - 1
    for k, m in enumerate(messages):
        m = dict(m)
        old = k < last - 1
        if m["role"] == "tool" and old:
            m["content"] = m["content"].split("\n", 1)[0] + " (details trimmed)"
        elif m["role"] == "user" and isinstance(m.get("content"), list) and (k < last or drop_images):
            parts = m["content"]
            m["content"] = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
            if k == last and any(p.get("type") == "image_url" for p in parts):
                m["content"] += " (images omitted: the context was full)"
        elif m["role"] == "user" and old and isinstance(m.get("content"), str) and m["content"].startswith("Tool res"):
            m["content"] = "\n".join(line for line in m["content"].splitlines() if line.startswith("[c"))
        out.append(m)
    return out


class Campaign:
    def __init__(self, cfg: RunConfig, run_id: str | None = None, client: LLMClient | None = None, transport=None):
        self.cfg = cfg
        self.run_id = run_id or new_run_id(cfg)
        self.run_dir = Path(cfg.runs_dir) / self.run_id
        self.ledger = Ledger(self.run_dir, self.run_id)
        self.resumed = bool(self.ledger.prior)
        self.scrub = self.unscrub = self.refmap = None
        if cfg.arm == "L":  # refcodes reach the model here, and never the disk
            self.refmap = RefcodeMap(cfg.data_dir)
            self.scrub, self.unscrub = self.refmap.scrub, self.refmap.unscrub
        cas_path = cfg.cassette_from if cfg.cassette == "replay" and cfg.cassette_from else self.run_dir / "cassette.jsonl"
        cassette = Cassette(cas_path, cfg.cassette, encode=self.scrub, decode=self.unscrub)
        self.client = client or LLMClient(cfg.base_url, cassette=cassette, transport=transport,
                                          **doctor_speeds(cfg.doctor_report))
        tools = ARM_TOOLS.get(cfg.arm, [])
        self.env = PoolEnv(cfg.data_dir, cfg.pool, self.run_dir / "img", cfg.budget, tools)
        self.round = 0
        self._cid = 0
        self.sources: dict[str, dict] = {}
        self.source_names: dict[str, str] = {}
        self.round_listing: list[str] = []
        self.round_calibration: ToolOutput | None = None
        self.n_rounds = 1 if cfg.arm == "A4" else cfg.rounds
        self.tool_cids: list[str] = []
        self.history: list[dict] = []
        self.p_hit: dict[str, float | None] = {}
        self.autofill = 0
        self.round_harness: set[str] = set()  # ids the harness (not the model) chose this round
        self.harness_ids: list[str] = []      # every bought id the harness chose, in purchase order
        self.aborted = False
        self.model_name = None
        self.t0 = time.monotonic()
        self.elapsed_offset = 0.0
        if self.resumed:
            ts = [datetime.fromisoformat(e["t"]) for e in (self.ledger.prior[0], self.ledger.prior[-1])]
            self.elapsed_offset = (ts[1] - ts[0]).total_seconds()
            self.model_name = (self.ledger.prior[0]["payload"].get("model") or {}).get("name")

    # ---- plumbing ----------------------------------------------------------------
    def next_cid(self) -> str:
        self._cid += 1
        return f"c{self._cid:04d}"

    def emit(self, kind: str, role: str, payload: dict, **kw) -> dict:
        return self.ledger.emit(kind, role, payload, self.round, **kw)

    def error(self, where: str, message: str, recovered: bool, call_id: str | None = None, **extra) -> dict:
        return self.emit("error", "system", {"where": where, "message": self._scrub(message)[:500],
                                             "recovered": recovered, **extra}, call_id=call_id)

    def elapsed(self) -> float:
        return self.elapsed_offset + time.monotonic() - self.t0

    def time_up(self) -> bool:
        if self.aborted:
            return True
        if self.ledger.replaying:
            ev = self.ledger.peek()
            hit = ev["kind"] == "error" and ev["payload"].get("where") == "time_cap"
        else:
            hit = self.elapsed() > self.cfg.max_minutes * 60
        if hit:
            self.aborted = True
            self.error("time_cap", f"time cap of {self.cfg.max_minutes:g} min reached; remaining lookups are "
                                   "autofilled", recovered=True)
        return hit

    def system_msg(self, round_: int) -> dict:
        bought = [{**self.env.bought[i], "S_mace": float(self.env.by_id.at[i, "S_mace"])} for i in self.env.order]
        rounds = self.n_rounds
        batch = self.cfg.budget if self.cfg.arm == "A4" else self.cfg.batch
        state = P.state_block(round_, rounds, self.cfg.budget, bought, self.history)
        text = P.system_prompt(goal=GOAL, budget=self.cfg.budget, rounds=rounds, batch=batch, tools=self.env.tools,
                               pool=P.pool_block(self.env.df), state=state)
        return {"role": "system", "content": text}

    def role_tools(self, role: str) -> tuple[list[dict] | None, str | None]:
        if self.cfg.action_mode != "native":
            return None, None
        if role == "director":
            return tool_schemas(self.env.tools), "auto"
        if self.cfg.shared_tools and role in ("proposer", "critic", "reporter"):
            return tool_schemas(self.env.tools), "none"
        return None, None

    def llm_call(self, role: str, messages: list[dict], *, schema: tuple[str, dict] | None = None,
                 tools: list[dict] | None = None, tool_choice: str | None = None,
                 log_role: str | None = None, max_tokens: int | None = None) -> tuple[LLMResult, str]:
        """One model call. An empty length-cut answer or a context overflow is retried once with a trimmed thread
        (older tool results cut to one line, images dropped); a length cut also lowers reasoning_strength a level."""
        if self.time_up():
            raise TimeUp
        strength = self.cfg.strengths.get(role, "medium")
        cid = self.next_cid()
        limit = max_tokens or MAX_TOKENS.get(role, 2048)
        lr = log_role or role
        try:
            try:
                res = self._send(role, lr, messages, cid, schema, tools, tool_choice, strength, limit, may_retry=True)
            except ToolsRejected:
                self.cfg.shared_tools = False  # for the rest of the run: only the Director gets tools
                tools = tool_choice = None
                cid = self.next_cid()
                res = self._send(role, lr, messages, cid, schema, None, None, strength, limit, may_retry=True)
            if not res.truncated:
                return res, cid
            cause = "length"
        except ContextOverflow:
            cause = "context"
        strength2 = lower_strength(strength) if cause == "length" else strength
        cid2 = self.next_cid()
        try:
            res = self._send(role, lr, trim_messages(messages, drop_images=True), cid2, schema, tools, tool_choice,
                             strength2, limit, may_retry=False)
        except ContextOverflow:
            return LLMResult("", "", [], "length", {}, {}, {}), cid2
        if cause == "length":
            lowered = f" and reasoning_strength {strength2}" if strength2 != strength else ""
            self.error(f"{role}.llm", "empty content with finish_reason=length; retried once with trimmed context"
                       + lowered, recovered=not res.truncated, call_id=cid, cause="length")
        return res, cid2

    def _send(self, role, log_role, messages, cid, schema, tools, tool_choice, strength, limit,
              may_retry: bool = True) -> LLMResult:
        """Sends one request, retrying model-server failures. Raises ContextOverflow (after logging it), or
        ModelDown after cfg.llm_attempts consecutive failures. Failures recorded in a prior ledger replay as failed
        attempts, so a call that failed before a crash is sent again on resume."""
        req = self.client.build_request(messages, tools=tools, tool_choice=tool_choice, schema=schema,
                                        reasoning_strength=strength, max_tokens=limit,
                                        seed=self.cfg.seed * 100_000 + self._cid)
        n_attempts = max(1, self.cfg.llm_attempts)
        failures = 0
        while True:
            prior = self.ledger.peek()
            if prior and prior["kind"] == "error" and prior.get("call_id") == cid:
                ev = self.ledger.emit("error", "system", prior["payload"], prior["round"], call_id=cid)
                if ev["payload"].get("cause") == "context":
                    raise ContextOverflow(ev["payload"].get("message", "context overflow"))
                if ev["payload"].get("cause") == "tools_rejected":
                    raise ToolsRejected()
                failures += 1
            else:
                try:
                    res = self.client.send(req)
                    break
                except ContextOverflow as e:
                    what = ("retrying once with trimmed context and no images" if may_retry else
                            "still too large after trimming; treated as an empty answer")
                    self.error(f"{role}.llm", f"context overflow ({e}); {what}", recovered=may_retry, call_id=cid,
                               cause="context")
                    raise
                except RequestRejected as e:
                    if tools and role != "director":
                        self.error(f"{role}.llm", f"{e}; resending without the shared tool list", recovered=True,
                                   call_id=cid, cause="tools_rejected")
                        raise ToolsRejected() from e
                    failures += 1
                    last = failures >= n_attempts
                    self.error(f"{role}.llm", f"{e} (attempt {failures} of {n_attempts}; "
                                              f"{'giving up' if last else 'retrying'})",
                               recovered=not last, call_id=cid, cause="transport", attempt=failures)
                except LLMError as e:
                    failures += 1
                    last = failures >= n_attempts
                    self.error(f"{role}.llm", f"{e} (attempt {failures} of {n_attempts}; "
                                              f"{'giving up' if last else 'retrying'})",
                               recovered=not last, call_id=cid, cause="transport", attempt=failures)
            if failures >= n_attempts:
                self.aborted = True
                self.error("llm_unavailable", f"the model server failed {failures} times in a row; the campaign "
                                              "stops and the remaining lookups are autofilled", recovered=True)
                raise ModelDown
            if self.time_up():
                raise TimeUp
            if not self.ledger.replaying:
                self._wait_for_server(failures)
        n_images = sum(1 for m in messages if isinstance(m.get("content"), list)
                       for p in m["content"] if p.get("type") == "image_url")
        self.emit("llm_call", log_role, {"model": self.model_name, "reasoning_strength": strength,
                                         "n_messages": len(messages), "n_images": n_images,
                                         "finish_reason": res.finish_reason, "schema": schema[0] if schema else None,
                                         "n_tool_calls": len(res.tool_calls)},
                  call_id=cid, tokens=res.tokens(), timings=res.event_timings(), dur_ms=res.dur_ms)
        if res.reasoning:
            self.emit("reasoning", log_role, {"text": self._scrub(res.reasoning)}, call_id=cid)
        if res.content.strip() and schema is None and role != "reporter":
            self.emit("message", log_role, {"text": self._scrub(res.content)}, call_id=cid)
        return res

    def _wait_for_server(self, failures: int):
        """Backoff after a failed call, then wait for /health to answer (bounded by llm_health_wait_s)."""
        time.sleep(min(MAX_BACKOFF_S, self.cfg.llm_backoff_s * 2 ** (failures - 1)))
        deadline = time.monotonic() + self.cfg.llm_health_wait_s
        while True:
            try:
                ready = self.client.get("/health", timeout=5.0).status_code == 200
            except Exception:  # noqa: BLE001 - any failure means "not ready yet"
                ready = False
            if ready or time.monotonic() >= deadline or self.elapsed() > self.cfg.max_minutes * 60:
                return
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))

    def _scrub(self, text: str) -> str:
        return self.scrub(text) if self.scrub else text

    def run_tool(self, role: str, name: str, args: dict | None, cid: str | None = None,
                 parse_error: str | None = None) -> tuple[str, ToolOutput]:
        cid = cid or self.next_cid()
        self.emit("tool_call", role, {"name": name, "args": args if args is not None else {}}, call_id=cid)
        if parse_error:
            out = ToolOutput(False, "error: arguments were not valid JSON", {"error": parse_error})
        elif role != "system" and name not in self.env.tools:
            out = ToolOutput(False, f"error: {name} is not available to the {role}", {"error": "tool not available"})
        else:
            out = self.env.call(name, args, cid)
        result = out.result
        if len(json.dumps(result)) > RESULT_LEDGER_CHARS:
            result = {"truncated": True, "text": json.dumps(result)[:RESULT_LEDGER_CHARS]}
        self.emit("tool_result", role, {"name": name, "ok": out.ok, "summary": out.summary[:120], "result": result,
                                        "image": out.image}, call_id=cid)
        if out.ok:
            self.sources[cid] = {"summary": out.summary, "result": out.result}
            self.source_names[cid] = name
            self.tool_cids.append(cid)
            self.round_listing.extend(out.ids)
            if name == "calibrate_oracle":
                self.emit("calibration", role, {"n": out.result["n"], "spearman": out.result["spearman"],
                                                "auc": out.result["auc"], "image": out.image}, call_id=cid)
                self.round_calibration = out
        return cid, out

    def image_bytes(self, rel: str) -> bytes:
        return (self.run_dir / rel).read_bytes()

    # ---- campaign ------------------------------------------------------------------
    def header(self):
        if self.ledger.replaying:
            ev = self.emit("header", "system", {})
            self.model_name = (ev["payload"].get("model") or {}).get("name")
            return
        model = attest.model_info(self.client, self.cfg.mode, self.cfg.doctor_report)
        self.model_name = model.get("name")
        a4 = self.cfg.arm == "A4"
        probe = self.cfg.arm in ("L", "L2")
        self.emit("header", "system", {
            "arm": self.cfg.arm, "pool": self.cfg.pool, "seed": self.cfg.seed,
            "budget": 0 if probe else self.cfg.budget, "rounds": 1 if a4 or probe else self.cfg.rounds,
            "batch": self.cfg.budget if a4 else (0 if probe else self.cfg.batch), "goal": GOAL, "mode": self.cfg.mode,
            "model": model, "machine": attest.machine_info(), "network": attest.network_state(),
            "prereg_sha": attest.prereg_sha(), "git_sha": attest.git_sha(),
            "config": {"action_mode": self.cfg.action_mode, "vision": self.cfg.vision,
                       "shared_tools": self.cfg.shared_tools, "max_minutes": self.cfg.max_minutes,
                       "chemist": self.cfg.chemist,
                       "max_director_calls": self.cfg.max_calls, "strengths": self.cfg.strengths,
                       "tools": self.env.tools, "cassette": self.client.cassette.mode,
                       "llm": {"timeout_floor_s": self.client.timeout, "decode_tps": self.client.decode_tps,
                               "prefill_tps": self.client.prefill_tps, "timeout_override_s": self.client.timeout_override,
                               "attempts": self.cfg.llm_attempts}}})

    def run(self) -> dict:
        self.header()
        if self.cfg.arm in ("L", "L2"):
            return self.run_probe()
        rounds = self.n_rounds
        batch = self.cfg.budget if self.cfg.arm == "A4" else self.cfg.batch
        try:
            for r in range(1, rounds + 1):
                self.round = r
                if self.time_up():
                    break
                self.run_round(r, min(batch, self.env.remaining))
        except CampaignStop:
            pass
        self.round = rounds + 1
        if self.env.remaining > 0:
            self.fill_remaining()
        self.write_report()
        if self.cfg.chemist and not self.aborted:
            self.chemist()
        return self.finish()

    def run_round(self, r: int, n: int):
        self.round_listing: list[str] = []
        self.round_calibration: ToolOutput | None = None
        self.round_harness = set()
        thread = [self.system_msg(r)]
        called = self.director(thread, r)
        if r >= 2 and "calibrate_oracle" in self.env.tools and "calibrate_oracle" not in called:
            note = "calibrate_oracle is mandatory from round 2 and the Director did not call it, so the lab called it."
            self.emit("message", "system", {"text": note})
            cid, out = self.run_tool("system", "calibrate_oracle", {})
            thread.append({"role": "user", "content": f"[lab] {note}\n{out.for_llm(cid, 'calibrate_oracle')}"})
        instruction = P.PROPOSER.format(round=r, rounds=self.n_rounds, n=n)
        if self.cfg.arm == "A4":
            instruction += P.PROPOSER_SHORT
        picks = self.propose(thread, r, n, instruction, set())
        self.emit("proposal", "proposer", {"picks": picks, "stage": "initial"})
        vetoed = []
        if self.cfg.arm != "A4":
            review = self.critique(picks, r)
            vetoed = [v["id"] for v in review["vetoes"]]
            if vetoed:
                lines = "\n".join(f"- {v['id']}: {v['reason']}" for v in review["vetoes"])
                keep = [p for p in picks if p["id"] not in vetoed]
                new = self.propose(thread, r, len(vetoed), P.REPLACE.format(k=len(vetoed), vetoes=lines),
                                   {p["id"] for p in picks})
                picks = keep + new
                self.emit("proposal", "proposer", {"picks": picks, "stage": "revised", "replaced": vetoed})
        ids = self.human_gate([p["id"] for p in picks], r)
        p_by_id = {p["id"]: p["p_hit"] for p in picks}
        mean_p = [p["p_hit"] for p in picks if p["p_hit"] is not None]
        rationale = (f"round {r}: {len(ids)} picks from the Proposer, mean p_hit "
                     f"{(sum(mean_p) / len(mean_p)) if mean_p else float('nan'):.2f}"
                     + (f"; critic vetoed {len(vetoed)}" if vetoed else ""))
        before = self.env.spent
        self.buy(ids, rationale, {i: p_by_id.get(i) for i in ids})
        self.harness_ids += [i for i in self.env.order[before:] if i in self.round_harness]
        new_hits = sum(self.env.bought[i]["hit"] for i in self.env.order[before:])
        self.history.append({"round": r, "ids": ids, "p_hits": [p_by_id.get(i) for i in ids], "hits": new_hits,
                             "vetoed": vetoed})

    # ---- roles ---------------------------------------------------------------------
    def director(self, thread: list[dict], r: int) -> set[str]:
        a4 = self.cfg.arm == "A4"
        if a4:
            text = P.DIRECTOR_ZERO_SHOT.format(batch=self.cfg.budget, max_calls=self.cfg.max_calls)
        else:
            calib = P.DIRECTOR_CALIBRATION if r >= 2 and "calibrate_oracle" in self.env.tools else ""
            text = P.DIRECTOR.format(round=r, rounds=self.cfg.rounds, batch=self.cfg.batch,
                                     max_calls=self.cfg.max_calls, calibration=calib)
        schema_mode = self.cfg.action_mode == "schema"
        if schema_mode:
            text += P.DIRECTOR_SCHEMA
        thread.append({"role": "user", "content": text})
        tools, choice = self.role_tools("director")
        schema = ("Steps", steps_schema(self.env.tools, 4)) if schema_mode else None
        used, called, images, pending_errors = 0, set(), 0, []
        for _ in range(MAX_DIRECTOR_TURNS):
            if used >= self.cfg.max_calls:
                break
            res, cid = self.llm_call("director", thread, schema=schema, tools=tools, tool_choice=choice)
            if res.truncated:
                break
            if schema_mode:
                try:
                    steps = Steps.model_validate_json(_strip_fences(res.content))
                except ValidationError as e:
                    pending_errors.append(f"invalid Steps JSON: {e.errors()[0]['msg'] if e.errors() else e}")
                    thread += [{"role": "assistant", "content": res.content},
                               {"role": "user", "content": "That was not valid JSON for the schema. Reply again."}]
                    if len(pending_errors) > 2:
                        break
                    continue
                for msg in pending_errors:
                    self.error("director.schema", msg, recovered=True)
                pending_errors = []
                thread.append({"role": "assistant", "content": res.content})
                if steps.note.strip():
                    self.emit("message", "director", {"text": steps.note.strip()}, call_id=cid)
                calls = [(s.tool, s.args, None) for s in steps.steps]
                if not calls:
                    break
            else:
                if not res.tool_calls:
                    thread.append({"role": "assistant", "content": res.content})
                    break
                calls = [(tc.name, tc.arguments, tc.parse_error) for tc in res.tool_calls]
            room = self.cfg.max_calls - used
            if len(calls) > room:
                self.error("director.max_calls", f"dropped {len(calls) - room} tool call(s) beyond the per-round cap "
                                                 f"of {self.cfg.max_calls}", recovered=True, call_id=cid)
                calls = calls[:room]
            outs = []
            for name, args, perr in calls:
                tcid, out = self.run_tool("director", name, args, parse_error=perr)
                outs.append((tcid, name, out, args))
                called.add(name)
            used += len(calls)
            img_parts = []
            if self.cfg.vision:
                for tcid, name, out, _ in outs:
                    if out.image and images < MAX_DIRECTOR_IMAGES:
                        img_parts += [{"type": "text", "text": f"Image from [{tcid}] {name}:"},
                                      image_part(self.image_bytes(out.image))]
                        images += 1
            if schema_mode:
                text = "Tool results:\n" + "\n".join(out.for_llm(t, n) for t, n, out, _ in outs)
                thread.append({"role": "user", "content": [{"type": "text", "text": text}] + img_parts
                               if img_parts else text})
                if steps.done:
                    break
            else:
                thread.append({"role": "assistant", "content": res.content or "", "tool_calls": [
                    {"id": t, "type": "function", "function": {"name": n, "arguments": json.dumps(a or {})}}
                    for t, n, _, a in outs]})
                thread += [{"role": "tool", "tool_call_id": t, "content": out.for_llm(t, n)} for t, n, out, _ in outs]
                if img_parts:
                    thread.append({"role": "user", "content": img_parts})
        for msg in pending_errors:
            self.error("director.schema", msg, recovered=False)
        return called

    def fallback_ranking(self, exclude: set[str]) -> list[str]:
        seen = list(dict.fromkeys(self.env.last_ranking + self.round_listing + self.env.mace_ranking()))
        return [i for i in seen if i not in self.env.bought and i not in exclude]

    def validate_batch(self, content: str, n: int, allowed: set[str]) -> tuple[list[dict], str | None]:
        try:
            batch = Batch.model_validate_json(_strip_fences(content))
        except ValidationError as e:
            return [], f"invalid JSON for the schema ({e.error_count()} error(s): {e.errors()[0]['msg']})"
        picks, seen, problems = [], set(), []
        known = set(self.tool_cids)
        for p in batch.picks:
            if p.id not in allowed:
                problems.append(f"{p.id} is not an allowed unbought id")
            elif p.id in seen:
                problems.append(f"{p.id} is duplicated")
            else:
                seen.add(p.id)
                picks.append({"id": p.id, "p_hit": round(p.p_hit, 4), "rationale": p.rationale[:300],
                              "evidence": [c for c in p.evidence if c in known][:4]})
        if len(batch.picks) != n:
            problems.append(f"expected {n} picks, got {len(batch.picks)}")
        return picks, "; ".join(problems) or None

    def propose(self, thread: list[dict], r: int, n: int, instruction: str, exclude: set[str]) -> list[dict]:
        allowed = [i for i in self.env.unbought() if i not in exclude]
        short = self.cfg.arm == "A4"
        schema = ("Batch", batch_schema(n, allowed, self.tool_cids, A4_RATIONALE_CHARS if short else 240))
        tools, choice = self.role_tools("proposer")
        thread.append({"role": "user", "content": instruction})
        errors, picks, err = [], [], "no answer"
        for attempt in range(3):
            res, _ = self.llm_call("proposer", thread, schema=schema, tools=tools, tool_choice=choice,
                                   max_tokens=max(MAX_TOKENS["proposer"], 1536 + 100 * n))
            thread.append({"role": "assistant", "content": res.content})
            picks, err = self.validate_batch(res.content, n, set(allowed))
            if err is None:
                break
            errors.append(err)
            if attempt < 2:
                thread.append({"role": "user", "content": P.PROPOSER_RETRY.format(error=err, n=n)})
        for e in errors:
            self.error("proposer.batch", e, recovered=err is None)
        if err is not None:
            picks = picks[:n]
            have = {p["id"] for p in picks}
            for i in self.fallback_ranking(exclude | have)[: n - len(picks)]:
                picks.append({"id": i, "p_hit": None, "rationale": "fallback: Director's ranking", "evidence": []})
                self.round_harness.add(i)
            self.error("proposer.fallback", f"filled {n - len(have)} pick(s) from the Director's ranking",
                       recovered=False)
        return picks

    def critique(self, picks: list[dict], r: int) -> dict:
        ids = [p["id"] for p in picks]
        top = max(picks, key=lambda p: (p["p_hit"] or 0, -ids.index(p["id"])))["id"]
        render = None
        if "render_structure" in self.env.tools:
            _, out = self.run_tool("critic", "render_structure", {"id": top})
            render = out if out.ok else None
        parity = self.round_calibration
        table = "\n".join(f"{p['id']} {p['p_hit']} | {p['rationale']} | {', '.join(p['evidence'])}" for p in picks)
        parts = []
        if self.cfg.vision:
            rline = P.CRITIC_RENDER.format(k=2 if parity else 1, id=top) if render else ""
            evidence = P.CRITIC_IMAGES.format(render=rline) if parity else P.CRITIC_NO_PARITY.format(render=rline)
            for o in (parity, render):
                if o is not None:
                    parts.append(image_part(self.image_bytes(o.image)))
        else:
            rline = f"\nRender of top pick {top}: {json.dumps(render.result)}" if render else ""
            if parity:
                rows = "\n".join(f"{x['id']} {x['S_mace']} {x['S_dft']} {'HIT' if x['hit'] else '-'}"
                                 for x in self.env.parity_rows())
                evidence = P.CRITIC_TEXT.format(table=rows, render=rline)
            else:
                evidence = P.CRITIC_NO_PARITY.format(render=rline)
        text = P.CRITIC.format(round=r, rounds=self.cfg.rounds, n=len(picks), batch=table, evidence=evidence)
        msgs = [self.system_msg(r), {"role": "user", "content": [{"type": "text", "text": text}] + parts
                                     if parts else text}]
        schema = ("Review", review_schema(ids, self.tool_cids))
        review, errors = None, []
        for attempt in range(3):
            tools, choice = self.role_tools("critic")
            res, _ = self.llm_call("critic", msgs, schema=schema, tools=tools, tool_choice=choice)
            try:
                rv = Review.model_validate_json(_strip_fences(res.content))
                bad = [v.id for v in rv.vetoes if v.id not in ids]
                if bad:
                    raise ValueError(f"vetoed ids not in the batch: {bad}")
                known = set(self.tool_cids)
                vetoes = list({v.id: {"id": v.id, "reason": v.reason[:300],
                                      "evidence": [c for c in v.evidence if c in known][:4]} for v in rv.vetoes}.values())
                review = {"visual_rho": rv.visual_rank_correlation if parity else None, "vetoes": vetoes[:3],
                          "notes": rv.notes[:600]}
                break
            except (ValidationError, ValueError) as e:
                errors.append(str(e).splitlines()[0][:300])
                if attempt < 2:
                    msgs = msgs + [{"role": "assistant", "content": res.content},
                                   {"role": "user", "content": f"That reply was invalid ({errors[-1]}). Reply again "
                                                               "with JSON only."}]
        for e in errors:
            self.error("critic.review", e, recovered=review is not None)
        if review is None:
            review = {"visual_rho": None, "vetoes": [], "notes": "critic output invalid after retries; no vetoes"}
        review["vision"] = self.cfg.vision
        self.emit("critique", "critic", review)
        return review

    def human_gate(self, ids: list[str], r: int) -> list[str]:
        if self.cfg.mode != "live":
            self.emit("human", "human", {"action": "auto", "ids": ids, "note": f"{self.cfg.mode} run: auto-approved"})
            return ids
        prior = self.ledger.peek()
        if prior and prior["kind"] == "human":
            decision = prior["payload"]
        else:
            pending = self.run_dir / "pending.json"
            pending.write_text(json.dumps({"round": r, "ids": ids}))
            decision = None
            while decision is None:
                if self.time_up():
                    raise TimeUp
                decision = self._read_approval(r)
                if decision is None:
                    time.sleep(self.cfg.approval_poll_s)
            pending.unlink(missing_ok=True)
            if decision.get("action") == "reject":
                rejected = [i for i in (decision.get("ids") or ids) if i in ids]
                decision = {"action": "reject", "ids": rejected, "note": decision.get("note", "")}
            else:
                decision = {"action": "approve", "ids": ids, "note": decision.get("note", "")}
        self.emit("human", "human", decision)
        if decision["action"] == "reject":
            keep = [i for i in ids if i not in decision["ids"]]
            repl = self.fallback_ranking(set(ids))[: len(ids) - len(keep)]
            self.round_harness.update(repl)
            return keep + repl
        return ids

    def _read_approval(self, r: int) -> dict | None:
        path = self.run_dir / "human.jsonl"
        if not path.exists():
            return None
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("round") == r:
                return d
        return None

    def buy(self, ids: list[str], rationale: str, p_hits: dict[str, float | None], role: str = "system"):
        new = [i for i in ids if i not in self.env.bought]
        self.emit("dft_order", role, {"ids": ids, "rationale": rationale, "spent": self.env.spent + len(new),
                                      "remaining": self.env.remaining - len(new)})
        cid, out = self.run_tool("system", "request_dft", {"ids": ids, "rationale": rationale})
        if not out.ok:
            self.error("request_dft", out.summary, recovered=False, call_id=cid)
            return
        for row in out.result["results"]:
            self.p_hit[row["id"]] = p_hits.get(row["id"])
        self.emit("dft_result", "system", {
            "results": [{k: row[k] for k in ("id", "E_CO2", "E_H2O", "hit")} for row in out.result["results"]],
            "hits_total": out.result["hits_total"], "spent": out.result["spent"]}, call_id=cid)

    def fill_remaining(self):
        """End-of-run autofill: the unbought MOFs with the lowest S_mace, for every arm (simple and arm-neutral)."""
        n = self.env.remaining
        self.round_listing = []
        ids = [i for i in self.env.mace_ranking() if i not in self.env.bought][:n]
        why = "the campaign stopped early" if self.aborted else "budget left after the last round"
        self.autofill = len(ids)
        before = self.env.spent
        self.buy(ids, f"autofill: {why}; {len(ids)} lookups filled from the S_mace ranking", {})
        self.harness_ids += [i for i in self.env.order[before:] if i in set(ids)]

    # ---- report ----------------------------------------------------------------------
    def evidence_digest(self) -> str:
        names = self.source_names
        keep = [c for c in self.sources if names[c] in ("request_dft", "calibrate_oracle")]
        keep += [c for c in self.sources if names[c] == "fit_surrogate"][-1:]
        return "\n".join(ToolOutput(True, self.sources[c]["summary"], self.sources[c]["result"]).for_llm(c, names[c])
                         for c in sorted(keep))

    def template_report(self) -> str:
        lines = []
        dft = [(c, s) for c, s in self.sources.items() if "results" in (s["result"] or {}) and "hits_total" in s["result"]]
        for c, s in dft:
            res = s["result"]
            lines.append(f"Order [{c}] bought {len(res['results'])} MOFs with {res['hits_new']} hit(s), "
                         f"{res['hits_total']} hit(s) in total [{c}].")
        if dft:
            c, s = dft[-1]
            lines.append(f"The campaign spent {s['result']['spent']} of {self.cfg.budget} DFT lookups [{c}].")
        best = min(((c, row) for c, s in dft for row in s["result"]["results"]), key=lambda x: x[1]["S"], default=None)
        if best:
            c, row = best
            lines.append(f"The best DAC score was {row['S']} eV for {row['id']} (E_CO2 {row['E_CO2']} eV) [{c}].")
        cal = [(c, s) for c, s in self.sources.items() if "spearman" in (s["result"] or {})]
        if cal:
            c, s = cal[-1]
            lines.append(f"Over {s['result']['n']} bought MOFs the cheap score had rank correlation "
                         f"{s['result']['spearman']} with DFT and hit-ranking area {s['result']['auc']} [{c}].")
        return " ".join(lines) or "No DFT lookups were made."

    def write_report(self):
        text, source = None, "template"
        if not self.aborted:
            msgs = [self.system_msg(self.n_rounds),
                    {"role": "user", "content": P.REPORTER.format(evidence=self.evidence_digest())}]
            try:
                tools, choice = self.role_tools("reporter")
                res, _ = self.llm_call("reporter", msgs, tools=tools, tool_choice=choice)
                text = res.content.strip() or None
                if text is None:
                    self.error("reporter", "empty report; using the template report", recovered=False)
                else:
                    source = "model"
            except CampaignStop:
                text = None
        text = text or self.template_report()
        self.emit("report", "reporter" if source == "model" else "system",
                  {"text": text, "source": source, "written_by": self.model_name if source == "model" else "template"})
        consts = (self.cfg.budget, self.cfg.batch, self.cfg.rounds, len(self.env.df))
        cc = claimcheck.check(text, self.sources, consts)
        self.emit("claimcheck", "system", cc)

    def chemist(self):
        """Prospective mode: mutation specs on the best bought MOFs. Only specs physics/mutate.py can execute are
        logged (status "proposed"); physics/prospective.py builds them later, outside the ledger."""
        env = self.env
        parents = chem.choose_parents(env.bought, env.order, env.by_id["natoms"].to_dict())
        opts = chem.options(parents, env.by_id["metals"].to_dict(), self.cfg.data_dir)
        if not opts:
            return
        rows = [f"{i} {env.by_id.at[i, 'formula']} {env.by_id.at[i, 'metals']} {env.bought[i]['S']} "
                f"{'HIT' if env.bought[i]['hit'] else '-'}" for i in opts]
        prompt = P.CHEMIST.format(parents=chem.describe(rows, opts), n=chem.MAX_MUTATIONS)
        msgs = [self.system_msg(self.n_rounds), {"role": "user", "content": prompt}]
        try:
            res, cid = self.llm_call("chemist", msgs, schema=("Mutations", mutations_schema(opts, chem.MAX_MUTATIONS)))
            muts = Mutations.model_validate_json(_strip_fences(res.content)).mutations
        except (ValidationError, CampaignStop) as e:
            self.error("chemist", (str(e).splitlines() or [type(e).__name__])[0][:300], recovered=False)
            return
        seen = set()
        for m in muts:
            why = chem.check(m, opts)
            spec = chem.to_spec(m)
            key = json.dumps([m.parent, spec], sort_keys=True)
            if why or key in seen:
                self.error("chemist", f"mutation skipped: {why or 'duplicate spec'}", recovered=True, call_id=cid)
                continue
            seen.add(key)
            self.emit("mutation", "chemist", {"parent": m.parent, "spec": spec, "status": "proposed",
                                              "note": m.rationale}, call_id=cid)

    def finish(self, extra: dict | None = None) -> dict:
        picks = list(self.env.order)
        flags = [bool(self.env.bought[i]["hit"]) for i in picks]
        p_hits = [self.p_hit.get(i) for i in picks]
        scored = [(p, h) for p, h in zip(p_hits, flags) if p is not None]
        summary = {
            "run_id": self.run_id, "arm": self.cfg.arm, "pool": self.cfg.pool, "seed": self.cfg.seed,
            "mode": self.cfg.mode, "model": self.model_name, "hits": sum(flags), "spent": len(picks),
            "picks": picks, "hit_flags": flags,
            "lookups_to_first_hit": next((k + 1 for k, h in enumerate(flags) if h), None),
            "brier": round(sum((p - h) ** 2 for p, h in scored) / len(scored), 4) if scored else None,
            "p_hits": p_hits, "duration_s": round(self.elapsed(), 1), "aborted": self.aborted,
            "autofill": self.autofill, "harness_picks": len(self.harness_ids), "harness_pick_ids": list(self.harness_ids),
            **(extra or {})}
        self.emit("summary", "system", summary)
        self.ledger.write_summary(summary)
        return summary

    # ---- leak probes -----------------------------------------------------------------
    def run_probe(self) -> dict:
        """L: rank raw CSD refcodes with nothing else. L2: rank from composition and descriptors only."""
        self.round = 1
        self.round_listing = []
        df = self.env.df
        rng = np.random.default_rng(self.cfg.seed)
        order = df["id"].tolist()
        rng.shuffle(order)
        if self.cfg.arm == "L":
            idmap = load_id_map(self.cfg.data_dir).set_index("id")["refcode"]
            key_of = {i: str(idmap[i]) for i in order}
            id_of = {v: k for k, v in key_of.items()}
            line = key_of.__getitem__
            template = P.PROBE_REFCODES
        else:
            key_of = {i: i for i in order}
            id_of = dict(key_of)
            by = df.set_index("id")

            def line(i):
                r = by.loc[i]
                return (f"{i} {r['formula']} {r['metals']} {'OMS' if r['oms'] else '-'} {r['pld_A']:.2f} "
                        f"{r['lcd_A']:.2f} {r['density_gcm3']:.3f} {r['void_fraction']:.3f} {r['asa_m2g']:.0f} "
                        f"{int(r['metal_cost_tier'])}")
            template = P.PROBE_DESCRIPTORS
        scores: dict[str, float] = {}
        try:
            for k in range(0, len(order), PROBE_CHUNK):
                chunk = order[k:k + PROBE_CHUNK]
                keys = [key_of[i] for i in chunk]
                msgs = [{"role": "system", "content": P.PROBE_SYSTEM},
                        {"role": "user", "content": template.format(n=len(chunk), items="\n".join(map(line, chunk)))}]
                got: dict[str, float] = {}
                for attempt in range(2):
                    res, _ = self.llm_call("probe", msgs, schema=("Scores", scores_schema(keys)), log_role="proposer")
                    try:
                        parsed = Scores.model_validate_json(_strip_fences(res.content)).scores
                    except ValidationError as e:
                        self.error("probe.scores", f"invalid scores JSON: {e.error_count()} error(s)",
                                   recovered=attempt == 0)
                        continue
                    for sc in parsed:
                        i = id_of.get(sc.key) or (self.refmap.resolve(sc.key) if self.refmap else None)
                        if i in chunk:
                            got[i] = sc.score
                    break
                missing = [i for i in chunk if i not in got]
                if missing:
                    self.error("probe.scores", f"{len(missing)} item(s) unscored; scored 0", recovered=False)
                for i in chunk:
                    scores[i] = float(got.get(i, 0))
        except CampaignStop:
            pass
        tiebreak = {i: float(rng.random()) for i in sorted(scores)}
        ranked = sorted(scores, key=lambda i: (-scores[i], tiebreak[i]))
        top = ranked[: self.cfg.budget]
        self.emit("proposal", "proposer", {"picks": [{"id": i, "p_hit": scores[i] / 10, "rationale": "",
                                                      "evidence": []} for i in top], "stage": "probe"})
        leak = score_probe(self.cfg.data_dir, scores)
        flags = [leak["hit"].get(i, False) for i in top]
        self.round = 2
        summary = {
            "run_id": self.run_id, "arm": self.cfg.arm, "pool": self.cfg.pool, "seed": self.cfg.seed,
            "mode": self.cfg.mode, "model": self.model_name, "hits": sum(flags), "spent": 0, "picks": top,
            "hit_flags": flags, "lookups_to_first_hit": next((k + 1 for k, h in enumerate(flags) if h), None),
            "brier": None, "p_hits": [scores[i] / 10 for i in top], "duration_s": round(self.elapsed(), 1),
            "aborted": self.aborted, "autofill": 0, "harness_picks": 0, "harness_pick_ids": [], "ranking": ranked, "scores": {i: scores[i] for i in ranked},
            "leak": {"auc": leak["auc"], "n": leak["n"], "n_hits": leak["n_hits"], "n_scored": len(scores)}}
        self.emit("summary", "system", summary)
        self.ledger.write_summary(summary)
        return summary
