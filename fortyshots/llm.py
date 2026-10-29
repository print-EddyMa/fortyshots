"""Loopback-only client for llama-server's OpenAI-compatible chat endpoint."""
import base64
import ipaddress
import json
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .cassette import Cassette, canonical

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MODEL_ALIAS = "muse-glimmer-30b"
MIN_TIMEOUT_S = 300.0          # floor of the per-call read timeout
DEFAULT_DECODE_TPS = 8.0       # assumed decode speed when doctor/report.json has none (measured: ~11-13 tok/s)
DEFAULT_PREFILL_TPS = 100.0    # assumed cold prefill speed when doctor/report.json has none
PREFILL_BASE_S = 30.0          # fixed part of the prefill allowance (slot setup, image encoding)
IMAGE_TOKENS = 1024            # prompt tokens charged per image when estimating prefill
DECODE_SLACK = 1.5             # the generation may run this much slower than measured
TIMEOUT_ENV = "FORTYSHOTS_TIMEOUT_S"
CONTEXT_OVERFLOW = re.compile(r"context", re.IGNORECASE)


class NonLoopbackError(ValueError):
    pass


class LLMError(RuntimeError):
    pass


class ContextOverflow(LLMError):
    """llama-server refused the prompt because it does not fit n_ctx (HTTP 400, "exceeds the available context
    size"). Deterministic for a given prompt, so the caller shrinks the prompt instead of resending it."""


def doctor_speeds(report: str | os.PathLike | None) -> dict:
    """{decode_tps, prefill_tps} measured by `fortyshots doctor` (None for each one the report lacks)."""
    out = {"decode_tps": None, "prefill_tps": None}
    if not report or not Path(report).exists():
        return out
    try:
        rep = json.loads(Path(report).read_text())
    except (OSError, json.JSONDecodeError):
        return out
    thr = next((c.get("detail") or {} for c in rep.get("checks") or [] if c.get("name") == "throughput"), {})
    dec = thr.get("decode_tok_s") or rep.get("decode_tok_s")
    if isinstance(dec, (int, float)) and dec > 0:
        out["decode_tps"] = float(dec)
    pps = [((r or {}).get("cold") or {}).get("prompt_per_second") for r in (thr.get("roles") or {}).values()]
    pps = [float(v) for v in pps if isinstance(v, (int, float)) and v > 0]
    if pps:
        out["prefill_tps"] = statistics.median(pps)
    return out


def assert_loopback(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    ok = host in LOOPBACK_HOSTS
    if not ok:
        try:
            ok = ipaddress.ip_address(host).is_loopback and host.startswith(("127.", "::1"))
        except ValueError:
            ok = False
    if not ok:
        raise NonLoopbackError(f"refusing non-loopback base_url {base_url!r}: fortyshots only talks to a local model")
    return base_url.rstrip("/")


def image_part(png: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    raw_arguments: str
    parse_error: str | None = None


@dataclass
class LLMResult:
    content: str
    reasoning: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: dict
    timings: dict
    raw: dict = field(repr=False)
    from_cassette: bool = False
    dur_ms: int = 0

    @property
    def truncated(self) -> bool:
        """Empty answer cut by the context or max_tokens: treated as a tool error by the caller."""
        return self.finish_reason == "length" and not self.content.strip() and not self.tool_calls

    @property
    def prompt_tokens(self) -> int:
        t = self.timings or {}
        if "prompt_n" in t:
            return int(t.get("prompt_n") or 0) + int(t.get("cache_n") or 0)
        return int((self.usage or {}).get("prompt_tokens") or 0)

    @property
    def completion_tokens(self) -> int:
        t = self.timings or {}
        return int(t.get("predicted_n") or (self.usage or {}).get("completion_tokens") or 0)

    @property
    def reasoning_tokens(self) -> int:
        """Reasoning share of the completion, by character proportion (llama-server reports no split)."""
        details = (self.usage or {}).get("completion_tokens_details") or {}
        if details.get("reasoning_tokens") is not None:
            return int(details["reasoning_tokens"])
        visible = len(self.content) + sum(len(c.raw_arguments) + len(c.name) for c in self.tool_calls)
        total = len(self.reasoning) + visible
        return round(self.completion_tokens * len(self.reasoning) / total) if total else 0

    def tokens(self) -> dict:
        return {"prompt": self.prompt_tokens, "completion": self.completion_tokens, "reasoning": self.reasoning_tokens}

    def event_timings(self) -> dict | None:
        t = self.timings or {}
        if not t:
            return None
        return {k: t.get(k) for k in ("prompt_n", "prompt_per_second", "predicted_n", "predicted_per_second", "cache_n")}


def parse_response(data: dict) -> LLMResult:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"malformed response: {str(data)[:200]}") from e
    msg = choice.get("message") or {}
    calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        args, err = None, None
        if isinstance(raw, dict):
            args, raw = raw, json.dumps(raw)
        else:
            try:
                args = json.loads(raw) if raw.strip() else {}
                if not isinstance(args, dict):
                    args, err = None, "arguments are not a JSON object"
            except json.JSONDecodeError as e:
                err = f"invalid JSON arguments: {e}"
        calls.append(ToolCall(tc.get("id") or f"call_{i}", fn.get("name") or "", args, raw, err))
    return LLMResult(content=msg.get("content") or "", reasoning=msg.get("reasoning_content") or "", tool_calls=calls,
                     finish_reason=choice.get("finish_reason"), usage=data.get("usage") or {},
                     timings=data.get("timings") or {}, raw=data)


class LLMClient:
    def __init__(self, base_url: str, *, timeout: float = MIN_TIMEOUT_S, retries: int = 3,
                 cassette: Cassette | None = None, transport: httpx.BaseTransport | None = None,
                 model: str = MODEL_ALIAS, decode_tps: float | None = None, prefill_tps: float | None = None):
        """`timeout` is the floor of the per-call read timeout; see timeout_for()."""
        self.base_url = assert_loopback(base_url)
        self.timeout = timeout
        self.decode_tps = decode_tps or DEFAULT_DECODE_TPS
        self.prefill_tps = prefill_tps or DEFAULT_PREFILL_TPS
        env = os.environ.get(TIMEOUT_ENV, "").strip()
        self.timeout_override = float(env) if env else None
        self.retries = retries
        self.cassette = cassette or Cassette(None, "off")
        self.model = model
        self.http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=10.0),
                                 transport=transport, trust_env=False)
        self.n_calls = 0

    def build_request(self, messages: list[dict], *, tools: list[dict] | None = None, tool_choice: str | None = None,
                      schema: tuple[str, dict] | None = None, reasoning_strength: str = "medium",
                      max_tokens: int = 2048, temperature: float = 1.0, seed: int | None = None) -> dict:
        req = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
               "stream": False, "cache_prompt": True, "chat_template_kwargs": {"reasoning_strength": reasoning_strength}}
        if tools:
            req["tools"] = tools
            req["parallel_tool_calls"] = True
            if tool_choice:
                req["tool_choice"] = tool_choice
        if schema:
            name, js = schema
            req["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": js, "strict": True}}
        if seed is not None:
            req["seed"] = seed
        return req

    def timeout_for(self, request: dict) -> float:
        """Read timeout for one non-streaming call: max(floor, prefill allowance + max_tokens / tok_s * 1.5).

        The prefill allowance is PREFILL_BASE_S plus the estimated prompt tokens (characters / 3, plus IMAGE_TOKENS
        per image) at the measured cold prefill speed. FORTYSHOTS_TIMEOUT_S replaces the whole formula."""
        if self.timeout_override:
            return self.timeout_override
        canon = canonical(request)
        n_images = sum(1 for m in canon.get("messages") or [] if isinstance(m.get("content"), list)
                       for p in m["content"] if p.get("type") == "image_url")
        prompt_tokens = len(json.dumps(canon.get("messages") or []) + json.dumps(canon.get("tools") or [])) / 3
        prompt_tokens += IMAGE_TOKENS * n_images
        prefill = PREFILL_BASE_S + prompt_tokens / self.prefill_tps
        decode = int(request.get("max_tokens") or 2048) / self.decode_tps * DECODE_SLACK
        return round(max(self.timeout, prefill + decode), 1)

    def chat(self, messages: list[dict], **kw) -> LLMResult:
        return self.send(self.build_request(messages, **kw))

    def send(self, request: dict) -> LLMResult:
        self.n_calls += 1
        t0 = time.monotonic()
        cached = self.cassette.lookup(request)
        if cached is not None:
            res = parse_response(cached)
            res.from_cassette = True
            res.dur_ms = int((cached.get("timings") or {}).get("prompt_ms", 0) + (cached.get("timings") or {}).get(
                "predicted_ms", 0))
            return res
        data = self._post("/v1/chat/completions", request, timeout=self.timeout_for(request))
        res = parse_response(data)
        res.dur_ms = int((time.monotonic() - t0) * 1000)
        self.cassette.record(request, data)
        return res

    def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        delay = 1.0
        timeout = timeout or self.timeout
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post(path, json=body, timeout=httpx.Timeout(timeout, connect=10.0))
                if r.status_code == 503 and attempt < self.retries:  # model still loading
                    time.sleep(delay)
                    delay *= 2
                    continue
                if r.status_code in (400, 413) and CONTEXT_OVERFLOW.search(r.text):
                    raise ContextOverflow(f"HTTP {r.status_code} from {path}: {r.text[:300]}")
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code} from {path}: {r.text[:300]}")
                return r.json()
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
                if attempt >= self.retries:
                    raise LLMError(f"cannot reach {self.base_url}{path}: {e}") from e
                time.sleep(delay)
                delay *= 2
            except httpx.TimeoutException as e:
                raise LLMError(f"timeout after {timeout:.0f}s on {path}") from e
        raise LLMError("unreachable")

    def get(self, path: str, timeout: float = 10.0) -> httpx.Response:
        return self.http.get(path, timeout=timeout)

    def post(self, path: str, body: dict, timeout: float | None = None) -> httpx.Response:
        return self.http.post(path, json=body, timeout=timeout or self.timeout)

    def close(self):
        self.http.close()
