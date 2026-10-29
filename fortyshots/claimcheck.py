"""Deterministic claim checker: every number and MOF id in report prose must match a tool output it cites."""
import re

CITE = re.compile(r"\[(c\d{4}(?:\s*[,;]\s*c\d{4})*)\]")
MOF_ID = re.compile(r"\bm[0-9a-f]{7}\b")
CHEM_TOKEN = re.compile(r"\b[A-Za-z_]*[A-Za-z]\d+[A-Za-z0-9_]*\b")  # CO2, H2O, E_CO2, NH2, Zn4O
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")
COUNTED = r"(?:lookups?|MOFs?|hits?|picks?|candidates?|labels?|experiments?|calls?|eV)"
# "Round 2", "order 3" are labels, not claims; "order 25 lookups" is a quantity.
ROUND_REF = re.compile(r"\b(?:[Rr]ounds?|[Oo]rders?|[Bb]atch(?:es)?)\s+([1-9])(?:\s*(?:and|to|-)\s*([1-9]))?\b"
                       r"(?!\s+" + COUNTED + r"\b)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])|\n+")
DEFAULT_CONSTANTS = (-0.5, 0.5, 0.1)


def _flatten(x, out_nums: list[float], out_strs: list[str]):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out_nums.append(float(x))
    elif isinstance(x, str):
        out_strs.append(x)
        out_nums.extend(float(m) for m in NUMBER.findall(x.replace("%", "")) if m not in ("-",))
    elif isinstance(x, dict):
        for v in x.values():
            _flatten(v, out_nums, out_strs)
    elif isinstance(x, list):
        out_nums.append(float(len(x)))
        for v in x:
            _flatten(v, out_nums, out_strs)


def _source_values(source) -> tuple[list[float], str]:
    nums, strs = [], []
    _flatten(source, nums, strs)
    return nums, " ".join(strs)


def _decimals(tok: str) -> int:
    t = tok.rstrip("%")
    return len(t.split(".")[1]) if "." in t else 0


def _matches(x: float, d: int, pct: bool, values: list[float]) -> bool:
    tol = 0.5 * 10 ** (-d) + 1e-9
    for v in values:
        if abs(x - v) <= tol or abs(x - abs(v)) <= tol:
            return True
        if pct and (abs(x / 100 - v) <= tol / 100 or abs(x - 100 * v) <= tol):
            return True
    return False


def check(text: str, sources: dict[str, object], constants: tuple[float, ...] = ()) -> dict:
    """sources: call_id -> tool output (result payload, may include the summary line)."""
    consts = [float(c) for c in (*DEFAULT_CONSTANTS, *constants)]
    claims, flagged = [], []
    text = text.replace("−", "-").replace("–", "-")
    for sent in (s.strip() for s in SENTENCE_SPLIT.split(text) if s and s.strip()):
        cites = [c.strip() for g in CITE.findall(sent) for c in re.split(r"[,;]", g)]
        body = CITE.sub(" ", sent)
        ids = MOF_ID.findall(body)
        body = MOF_ID.sub(" ", body)
        body = ROUND_REF.sub(" ", body)
        body = CHEM_TOKEN.sub(" ", body)
        nums = NUMBER.findall(body)
        if not ids and not nums:
            continue
        cited = [sources[c] for c in cites if c in sources]
        values, strings = _source_values(cited)
        unknown = [c for c in cites if c not in sources]
        for tok in nums:
            x = float(tok.rstrip("%"))
            claim = {"claim": tok, "sentence": sent[:200]}
            claims.append(claim)
            if _matches(x, _decimals(tok), tok.endswith("%"), values):
                continue
            if _matches(x, _decimals(tok), False, consts):
                continue
            reason = (f"cites unknown call id(s) {', '.join(unknown)}" if unknown and not cited
                      else "no citation" if not cites else f"{tok} not found in cited output(s) {', '.join(cites)}")
            flagged.append({"claim": f"{tok} | {sent[:160]}", "reason": reason})
        for mid in ids:
            claims.append({"claim": mid, "sentence": sent[:200]})
            if mid in strings:
                continue
            reason = "no citation" if not cites else f"{mid} not found in cited output(s) {', '.join(cites)}"
            flagged.append({"claim": f"{mid} | {sent[:160]}", "reason": reason})
    return {"n_claims": len(claims), "verified": len(claims) - len(flagged), "flagged": flagged}


def sources_from_events(events: list[dict]) -> dict[str, object]:
    return {e["call_id"]: {"summary": e["payload"].get("summary"), "result": e["payload"].get("result")}
            for e in events if e.get("kind") == "tool_result" and e.get("call_id") and e["payload"].get("ok")}
