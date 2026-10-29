"""Deterministic fake llama-server with the same response shape (reasoning_content, tool_calls, timings).

The policy reads only the request: role instruction, offered tools, tool results already in the messages, and the
requested schema. It never reads labels; a given request always yields the same response.
"""
import base64
import hashlib
import io
import json
import random
import re

from .cassette import request_key

MOCK_MODEL = "muse-glimmer-30b-mock"
MOCK_GGUF = "mock/Muse-Glimmer-30B-MOCK.gguf"
MOCK_BUILD = "b0000-mock"
REASONING_BUDGET = 1024
STRENGTH_WORDS = {"low": 50, "medium": 110, "high": 260, "xhigh": 600}

RESULT_BLOCK = re.compile(r"\[(c\d{4})\] (\w+): [^\n]*\n(\{.*?\})(?=\n\[c\d{4}\]|\n*$)", re.DOTALL)
BOUGHT_ROW = re.compile(r"^(m[0-9a-f]{7}) (-?\d+\.\d+) (-?\d+\.\d+) (-?\d+\.\d+) (-?\d+\.\d+) (HIT|-)$", re.MULTILINE)
BATCH_ROW = re.compile(r"^(m[0-9a-f]{7}) ([\d.]+|None) \| (.*?) \| (.*)$", re.MULTILINE)
FILLER = ("I weigh what the tool results show against what is still unknown. The budget is fixed, so each lookup "
          "should either test a promising candidate or teach something about the pool. ")


# ---- request parsing ---------------------------------------------------------------------
def _text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if p.get("type") == "text")
    return c or ""


def _images(m: dict) -> list[bytes]:
    c = m.get("content")
    if not isinstance(c, list):
        return []
    out = []
    for p in c:
        url = (p.get("image_url") or {}).get("url", "") if p.get("type") == "image_url" else ""
        if url.startswith("data:") and "," in url:
            out.append(base64.b64decode(url.split(",", 1)[1]))
    return out


def tool_blocks(messages: list[dict]) -> list[dict]:
    """Every '[cNNNN] name: summary\\n{json}' tool result in the conversation, in order."""
    blocks = []
    for m in messages:
        if m.get("role") not in ("tool", "user"):
            continue
        for cid, name, body in RESULT_BLOCK.findall(_text(m)):
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            blocks.append({"cid": cid, "name": name, "data": data, "rows": _rows(data)})
    return blocks


def _rows(data) -> list[dict]:
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        return [dict(zip(data["columns"], r)) for r in data["rows"]]
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return []


def bought_rows(messages: list[dict]) -> list[dict]:
    sys_text = _text(messages[0]) if messages and messages[0].get("role") == "system" else ""
    return [{"id": m[0], "S_mace": float(m[1]), "E_CO2": float(m[2]), "S": float(m[4]), "hit": m[5] == "HIT"}
            for m in BOUGHT_ROW.findall(sys_text)]


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = float(k)
        return r
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def _last_instruction(messages: list[dict], prefix: str) -> int:
    for k in range(len(messages) - 1, -1, -1):
        if messages[k].get("role") == "user" and _text(messages[k]).startswith(prefix):
            return k
    return -1


# ---- policies -------------------------------------------------------------------------------
class Policy:
    def __init__(self, req: dict):
        self.req = req
        self.messages = req.get("messages") or []
        self.rng = random.Random(request_key(req))
        self.tools = [t["function"]["name"] for t in req.get("tools") or []]
        rf = req.get("response_format") or {}
        self.schema_name = (rf.get("json_schema") or {}).get("name")
        self.schema = (rf.get("json_schema") or {}).get("schema") or rf.get("schema") or {}
        self.last = _text(self.messages[-1]) if self.messages else ""
        self.strength = (req.get("chat_template_kwargs") or {}).get("reasoning_strength", "high")
        self.blocks = tool_blocks(self.messages)

    def reasoning(self, gist: str) -> str:
        words = STRENGTH_WORDS.get(self.strength, 110)
        text = gist.strip() + " "
        while len(text.split()) < words:
            text += FILLER
        return " ".join(text.split()[: max(words, len(gist.split()))])

    def call(self, name: str, args: dict) -> dict:
        return {"id": f"call_{self.rng.getrandbits(48):012x}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    # director -------------------------------------------------------------------
    def director_plan(self) -> tuple[list[tuple[str, dict]], str, str]:
        k = _last_instruction(self.messages, "ROLE: Director")
        instr = _text(self.messages[k]) if k >= 0 else ""
        m = re.search(r"round (\d+) of", instr)
        rnd = int(m.group(1)) if m else 1
        turn = sum(1 for x in self.messages[k + 1:] if x.get("role") == "assistant")
        round_blocks = tool_blocks(self.messages[k + 1:])
        seen = [r["id"] for b in round_blocks for r in b["rows"] if "id" in r]
        bought = bought_rows(self.messages)
        allowed = set(self.tools) if self.tools else set(re.findall(r"^- (\w+)\(", _text(self.messages[0]), re.MULTILINE))
        limit = self.rng.choice([12, 15, 20])
        plan: list[tuple[str, dict]] = []
        if turn == 0:
            plan.append(("query_pool", {"sort_by": "S_mace", "ascending": True, "limit": limit,
                                        "filters": {"exclude_bought": True}}))
            plan.append(("query_pool", {"sort_by": self.rng.choice(["E_CO2_mace", "pld_A"]), "ascending": True,
                                        "limit": 10, "filters": {"oms": True, "pld_max": 6.5}}))
            if rnd >= 2 and rnd != 3:  # round 3 skips it so the lab's mandatory call path is exercised
                plan.append(("calibrate_oracle", {}))
        elif turn == 1:
            if len(bought) >= 5:
                plan.append(("fit_surrogate", {"kappa": self.rng.choice([0.5, 1.0, 1.5]), "top_k": 15}))
            if seen:
                plan.append(("get_descriptors", {"ids": seen[:6]}))
                if rnd == 1 or self.rng.random() < 0.5:
                    plan.append(("render_structure", {"id": seen[0]}))
        elif turn == 2 and seen:
            plan.append(("cheap_score", {"ids": seen[-5:]}))
        plan = [(n, a) for n, a in plan if n in allowed]
        best = seen[0] if seen else None
        cites = ", ".join(sorted({b["cid"] for b in round_blocks})[:3])
        note = (f"Explored {len(seen)} candidate rows this round; {best} leads the shortlist" if best else
                "Explored the pool") + (f" [{cites}]." if cites else ".")
        gist = (f"Round {rnd}, turn {turn}. {len(bought)} DFT labels so far with "
                f"{sum(b['hit'] for b in bought)} hits. " + ("Next: " + ", ".join(n for n, _ in plan) + "."
                                                            if plan else "Enough evidence; summarising."))
        return plan, note, gist

    def director_native(self) -> dict:
        plan, note, gist = self.director_plan()
        if plan:
            return {"content": None, "reasoning": self.reasoning(gist),
                    "tool_calls": [self.call(n, a) for n, a in plan]}
        return {"content": note, "reasoning": self.reasoning(gist)}

    def director_schema(self) -> dict:
        plan, note, gist = self.director_plan()
        body = {"steps": [{"tool": n, "args": a} for n, a in plan], "done": not plan, "note": "" if plan else note}
        return {"content": json.dumps(body), "reasoning": self.reasoning(gist)}

    # proposer -------------------------------------------------------------------
    def batch(self) -> dict:
        picks_s = self.schema["properties"]["picks"]
        n = picks_s.get("minItems", 10)
        item = picks_s["items"]["properties"]
        enum = item["id"].get("enum") or []
        cid_enum = set(item["evidence"]["items"].get("enum") or [])
        allowed = set(enum)
        cands: list[tuple[str, dict, str, str]] = []
        order = sorted(self.blocks, key=lambda b: (b["name"] != "fit_surrogate", -int(b["cid"][1:])))
        for b in order:
            for r in b["rows"]:
                if r.get("id") in allowed and r["id"] not in {c[0] for c in cands}:
                    cands.append((r["id"], r, b["cid"], b["name"]))
        rest = [i for i in enum if i not in {c[0] for c in cands}]
        self.rng.shuffle(rest)
        cands += [(i, {}, "", "") for i in rest]
        picks = []
        for k, (i, row, cid, src) in enumerate(cands[:n]):
            base = 0.32 if src == "fit_surrogate" else 0.16 if src else 0.05
            p = min(0.9, max(0.01, base * (1 - 0.03 * k) + self.rng.uniform(-0.04, 0.04)))
            if "pred_S" in row:
                why = f"Surrogate predicts S {row['pred_S']} eV (spread {row['std']})."
            elif "S_mace" in row:
                why = f"Cheap score S_mace {row['S_mace']} eV" + (", open metal site." if row.get("oms") else ".")
            else:
                why = "Unexplored region of the pool."
            picks.append({"id": i, "p_hit": round(p, 2), "rationale": why,
                          "evidence": [cid] if cid and (not cid_enum or cid in cid_enum) else []})
        retrying = self.last.startswith("That reply was not a valid batch")
        if not retrying and n >= 3 and self.rng.random() < 0.1:
            picks[-1] = dict(picks[0])  # occasional duplicate exercises validation and retry
        gist = (f"I need {n} picks. {len(cands)} candidates are available; I rank surrogate suggestions first, then "
                f"low cheap scores, and keep p_hit modest because hits are rare.")
        return {"content": json.dumps({"picks": picks}), "reasoning": self.reasoning(gist)}

    # critic -------------------------------------------------------------------
    def review(self) -> dict:
        veto_s = self.schema["properties"]["vetoes"]["items"]["properties"]
        batch_ids = veto_s["id"].get("enum") or []
        rows = {m[0]: m for m in BATCH_ROW.findall(self.last)}
        bought = bought_rows(self.messages)
        has_plot = "parity plot for every MOF" in self.last or "Parity data" in self.last
        rho = _spearman([b["S_mace"] for b in bought], [b["S"] for b in bought]) if has_plot else None
        visual = None if rho is None else round(max(-1.0, min(1.0, rho + self.rng.gauss(0, 0.12))), 2)
        k = self.rng.choice([0, 0, 1, 1, 2])

        def p_of(i):
            try:
                return float(rows[i][1])
            except (KeyError, ValueError):
                return 0.0
        weakest = sorted(batch_ids, key=lambda i: (p_of(i), i))[:k]
        vetoes = []
        for i in weakest:
            ev = [c.strip() for c in rows[i][3].split(",") if c.strip()] if i in rows else []
            vetoes.append({"id": i, "reason": f"Lowest p_hit in the batch ({p_of(i)}) and thin evidence.",
                           "evidence": ev[:2]})
        notes = ("Read the parity plot: " + (f"points scatter, estimated rank correlation {visual}."
                                              if visual is not None else "no plot yet."))
        gist = f"Batch of {len(batch_ids)}. {len(bought)} labels bought. Vetoing {k}."
        return {"content": json.dumps({"visual_rank_correlation": visual, "vetoes": vetoes, "notes": notes}),
                "reasoning": self.reasoning(gist)}

    # reporter -------------------------------------------------------------------
    def report(self) -> dict:
        blocks = tool_blocks([{"role": "user", "content": self.last.split("EVIDENCE", 1)[-1]}])
        dft = [b for b in blocks if b["name"] == "request_dft"]
        cal = [b for b in blocks if b["name"] == "calibrate_oracle"]
        sur = [b for b in blocks if b["name"] == "fit_surrogate"]
        out = []
        for k, b in enumerate(dft, 1):
            d = b["data"]
            out.append(f"Order {k} bought {len(d['results'])} MOFs and DFT confirmed {d['hits_new']} hit(s) "
                       f"[{b['cid']}].")
        if dft:
            d = dft[-1]["data"]
            out.append(f"In total the campaign spent {d['spent']} lookups and found {d['hits_total']} hits "
                       f"[{dft[-1]['cid']}].")
            best_b, best = min(((b, r) for b in dft for r in b["data"]["results"]), key=lambda x: x[1]["S"])
            out.append(f"The strongest candidate was {best['id']} with E_CO2 {best['E_CO2']} eV and S {best['S']} eV "
                       f"[{best_b['cid']}].")
        if cal and cal[-1]["data"].get("spearman") is not None:
            d = cal[-1]["data"]
            out.append(f"Against the bought labels the cheap simulator ranked candidates with spearman "
                       f"{d['spearman']} over {d['n']} MOFs [{cal[-1]['cid']}], so the later rounds leaned on the "
                       f"DFT labels themselves.")
        if sur and sur[-1]["rows"]:
            ids = [r["id"] for r in sur[-1]["rows"][:2]]
            out.append(f"Next I would test {' and '.join(ids)}, the surrogate's top suggestions [{sur[-1]['cid']}].")
        text = " ".join(out) or "The campaign made no DFT lookups."
        return {"content": text, "reasoning": self.reasoning("Summarise orders, hits, the calibration, next steps.")}

    # probes and doctor ------------------------------------------------------------------
    def scores(self) -> dict:
        keys = self.schema["properties"]["scores"]["items"]["properties"]["key"].get("enum") or []
        out = [{"key": k, "score": int(hashlib.sha256((k + str(self.req.get("seed"))).encode()).digest()[0] % 11)}
               for k in keys]
        return {"content": json.dumps({"scores": out}), "reasoning": self.reasoning(f"Scoring {len(keys)} items.")}

    def mutations(self) -> dict:
        parents = self.schema["properties"]["mutations"]["items"]["properties"]["parent"].get("enum") or []
        muts = [{"parent": p, "kind": "functionalize", "site": "aromatic C-H", "group": self.rng.choice(["NH2", "OH"]),
                 "new_metal": "", "rationale": "An amine or hydroxyl near the metal site can add a CO2 anchor."}
                for p in parents[:2]]
        return {"content": json.dumps({"mutations": muts}), "reasoning": self.reasoning("Two functionalizations.")}

    def doctor_vision(self) -> dict:
        imgs = [b for m in self.messages for b in _images(m)]
        count = count_red_blobs(imgs[0]) if imgs else 0
        return {"content": json.dumps({"count": count}), "reasoning": self.reasoning("Counting red dots.")}

    def doctor_math(self) -> dict:
        m = re.search(r"(\d+)\s*\*\s*(\d+)", self.last)
        return {"content": json.dumps({"answer": int(m.group(1)) * int(m.group(2)) if m else 0}),
                "reasoning": self.reasoning("Multiply.")}

    def generic_tools(self) -> dict:
        if self.messages and self.messages[-1].get("role") == "tool":
            result = self.last.split(":")[-1].strip()
            return {"content": f"The result is {result}.", "reasoning": self.reasoning("The tool answered.")}
        calls = []
        if "add" in self.tools:
            calls = [self.call("add", {"a": int(a), "b": int(b)}) for a, b in re.findall(r"(\d+) \+ (\d+)", self.last)]
        if not calls:
            calls = [self.call(self.tools[0], {})]
        return {"content": None, "reasoning": self.reasoning("Use the tool."), "tool_calls": calls}

    def chat(self) -> dict:
        if "READY" in self.last:
            return {"content": "READY", "reasoning": self.reasoning("The user wants one word.")}
        return {"content": "OK.", "reasoning": self.reasoning("Short answer.")}

    def respond(self) -> dict:
        lab_tools = bool(set(self.tools) & {"query_pool", "cheap_score"})
        choice = self.req.get("tool_choice", "auto")
        dispatch = {"Steps": self.director_schema, "Batch": self.batch, "Review": self.review, "Scores": self.scores,
                    "Mutations": self.mutations, "doctor_vision": self.doctor_vision, "doctor_math": self.doctor_math}
        if self.schema_name in dispatch:
            return dispatch[self.schema_name]()
        if self.tools and choice != "none":
            return self.director_native() if lab_tools else self.generic_tools()
        if self.last.startswith("ROLE: Reporter"):
            return self.report()
        return self.chat()


def count_red_blobs(png: bytes) -> int:
    import matplotlib.image as mpimg
    from scipy import ndimage

    img = mpimg.imread(io.BytesIO(png), format="png")
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mask = (r > 0.7) & (g < 0.35) & (b < 0.35)
    _, n = ndimage.label(mask)
    return int(n)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def respond(req: dict) -> dict:
    """One /v1/chat/completions response for a request dict."""
    pol = Policy(req)
    out = pol.respond()
    content, reasoning, calls = out.get("content"), out.get("reasoning", ""), out.get("tool_calls") or []
    max_tokens = int(req.get("max_tokens") or 4096)
    r_tok = min(_tokens(reasoning), REASONING_BUDGET)
    reasoning = reasoning[: r_tok * 4]
    v_tok = _tokens((content or "") + "".join(c["function"]["arguments"] for c in calls)) if (content or calls) else 0
    finish = "tool_calls" if calls else "stop"
    if r_tok + v_tok > max_tokens:
        reasoning, content, calls, finish = reasoning[: max_tokens * 4], "", [], "length"
        r_tok, v_tok = min(r_tok, max_tokens), 0
    msgs = req.get("messages") or []
    n_img = sum(len(_images(m)) for m in msgs)
    total_prompt = _tokens(json.dumps([_text(m) for m in msgs])) + 256 * n_img + 40 * len(pol.tools)
    cache_n = min(total_prompt - 1, _tokens(json.dumps([_text(m) for m in msgs[:-1]]))) if len(msgs) > 1 else 0
    prompt_n = total_prompt - cache_n
    pps, tps = 180.0 + pol.rng.uniform(-10, 10), 12.5 + pol.rng.uniform(-1, 1)
    predicted = r_tok + v_tok
    message = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if calls:
        message["tool_calls"] = calls
    key = request_key(req)
    return {
        "id": "chatcmpl-" + key[:24], "object": "chat.completion", "created": 1761696000 + int(key[:6], 16) % 86400,
        "model": req.get("model") or MOCK_MODEL, "system_fingerprint": MOCK_BUILD,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": total_prompt, "completion_tokens": predicted, "total_tokens": total_prompt + predicted},
        "timings": {"cache_n": cache_n, "prompt_n": prompt_n, "prompt_ms": round(prompt_n / pps * 1000, 3),
                    "prompt_per_token_ms": round(1000 / pps, 3), "prompt_per_second": round(pps, 3),
                    "predicted_n": predicted, "predicted_ms": round(predicted / tps * 1000, 3),
                    "predicted_per_token_ms": round(1000 / tps, 3), "predicted_per_second": round(tps, 3)},
    }


PROPS = {
    "default_generation_settings": {"n_ctx": 32768, "params": {"temperature": 1.0, "top_p": 0.95, "top_k": 64}},
    "total_slots": 1, "model_path": MOCK_GGUF, "model_alias": MOCK_MODEL, "build_info": MOCK_BUILD,
    "modalities": {"vision": True, "audio": False}, "chat_template": "{# mock template #}",
}


def create_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse

    app = FastAPI(title="fortyshots mock llama-server")
    state = {"requests": 0}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/props")
    def props():
        return PROPS

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MOCK_MODEL, "object": "model", "owned_by": "llamacpp",
                                            "meta": {"n_ctx_train": 131072, "n_params": 30_000_000_000}}]}

    @app.post("/tokenize")
    async def tokenize(request: Request):
        body = await request.json()
        return {"tokens": list(range(_tokens(body.get("content", ""))))}

    @app.get("/metrics")
    def metrics():
        return PlainTextResponse(f"llamacpp:requests_processing 0\nllamacpp:n_requests_total {state['requests']}\n")

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        state["requests"] += 1
        return respond(await request.json())

    return app


def transport():
    """httpx transport that answers in-process, for tests and server-less mock runs."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/chat/completions":
            return httpx.Response(200, json=respond(json.loads(request.content)))
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/props":
            return httpx.Response(200, json=PROPS)
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": MOCK_MODEL, "object": "model"}]})
        if path == "/tokenize":
            return httpx.Response(200, json={"tokens": list(range(_tokens(json.loads(request.content).get("content", ""))))})
        return httpx.Response(404, json={"error": "not found"})
    return httpx.MockTransport(handler)


def serve(host: str = "127.0.0.1", port: int = 8080):
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
