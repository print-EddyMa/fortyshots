"""Structured outputs: pydantic models for validation and the JSON schemas sent as response_format."""
from pydantic import BaseModel, Field, ValidationError  # noqa: F401  (re-exported)

from ..tools import TOOL_SPECS


class Pick(BaseModel):
    id: str
    p_hit: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    evidence: list[str] = []


class Batch(BaseModel):
    picks: list[Pick]


class Veto(BaseModel):
    id: str
    reason: str = ""
    evidence: list[str] = []


class Review(BaseModel):
    visual_rank_correlation: float | None = Field(default=None, ge=-1.0, le=1.0)
    vetoes: list[Veto] = Field(default=[], max_length=3)
    notes: str = ""


class Step(BaseModel):
    tool: str
    args: dict = {}


class Steps(BaseModel):
    steps: list[Step] = []
    done: bool = False
    note: str = ""


class Score(BaseModel):
    key: str
    score: int = Field(ge=0, le=10)


class Scores(BaseModel):
    scores: list[Score]


class Mutation(BaseModel):
    parent: str
    kind: str
    site: str = ""
    group: str = ""
    new_metal: str = ""
    rationale: str = ""


class Mutations(BaseModel):
    mutations: list[Mutation] = Field(default=[], max_length=3)


def _strs(enum: list[str] | None = None, max_items: int | None = None) -> dict:
    items = {"type": "string", **({"enum": enum} if enum else {})}
    return {"type": "array", "items": items, **({"maxItems": max_items} if max_items else {})}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


def batch_schema(n: int, ids: list[str], call_ids: list[str]) -> dict:
    pick = _obj({"id": {"type": "string", "enum": ids},
                 "p_hit": {"type": "number", "minimum": 0, "maximum": 1},
                 "rationale": {"type": "string", "maxLength": 240},
                 "evidence": _strs(call_ids or None, 4)})
    return _obj({"picks": {"type": "array", "items": pick, "minItems": n, "maxItems": n}})


def review_schema(batch_ids: list[str], call_ids: list[str]) -> dict:
    veto = _obj({"id": {"type": "string", "enum": batch_ids}, "reason": {"type": "string", "maxLength": 240},
                 "evidence": _strs(call_ids or None, 4)})
    return _obj({"visual_rank_correlation": {"type": ["number", "null"], "minimum": -1, "maximum": 1},
                 "vetoes": {"type": "array", "items": veto, "maxItems": 3},
                 "notes": {"type": "string", "maxLength": 600}})


def steps_schema(tools: list[str], max_steps: int) -> dict:
    variants = [_obj({"tool": {"type": "string", "const": t}, "args": TOOL_SPECS[t]["parameters"]}) for t in tools]
    return _obj({"steps": {"type": "array", "items": {"anyOf": variants}, "maxItems": max_steps},
                 "done": {"type": "boolean"}, "note": {"type": "string", "maxLength": 600}})


def scores_schema(keys: list[str]) -> dict:
    item = _obj({"key": {"type": "string", "enum": keys}, "score": {"type": "integer", "minimum": 0, "maximum": 10}})
    return _obj({"scores": {"type": "array", "items": item, "minItems": len(keys), "maxItems": len(keys)}})


def mutations_schema(parents: list[str]) -> dict:
    m = _obj({"parent": {"type": "string", "enum": parents},
              "kind": {"type": "string", "enum": ["functionalize", "metal_swap"]},
              "site": {"type": "string"}, "group": {"type": "string", "enum": ["NH2", "OH", "F", "CH3", ""]},
              "new_metal": {"type": "string"}, "rationale": {"type": "string", "maxLength": 240}})
    return _obj({"mutations": {"type": "array", "items": m, "maxItems": 3}})
