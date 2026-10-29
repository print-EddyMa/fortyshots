"""Record/replay of model calls, keyed by sha256 of the canonical request (images replaced by their hash)."""
import base64
import hashlib
import json
import os
from pathlib import Path

MODES = ("off", "record", "replay")


class CassetteMiss(KeyError):
    pass


def _image_hash(url: str) -> str:
    if url.startswith("data:") and "," in url:
        return "sha256:" + hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest()
    return url


def canonical(request: dict) -> dict:
    """The request with every data-URI image replaced by sha256 of its bytes."""
    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "image_url" and isinstance(x.get("image_url"), dict):
                return {"type": "image_url", "image_url": {"url": _image_hash(x["image_url"].get("url", ""))}}
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(request)


def request_key(request: dict) -> str:
    blob = json.dumps(canonical(request), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def request_meta(request: dict) -> dict:
    """Shape of the request without any message text (keeps refcodes and prompts out of the cassette)."""
    msgs = request.get("messages", [])
    images = [p["image_url"]["url"] for m in canonical(request).get("messages", []) if isinstance(m.get("content"), list)
              for p in m["content"] if p.get("type") == "image_url"]
    rf = request.get("response_format") or {}
    return {
        "n_messages": len(msgs),
        "roles": [m.get("role") for m in msgs],
        "tools": [t["function"]["name"] for t in request.get("tools") or []],
        "tool_choice": request.get("tool_choice"),
        "schema": (rf.get("json_schema") or {}).get("name"),
        "reasoning_strength": (request.get("chat_template_kwargs") or {}).get("reasoning_strength"),
        "max_tokens": request.get("max_tokens"),
        "seed": request.get("seed"),
        "images": images,
    }


class Cassette:
    def __init__(self, path: str | os.PathLike | None, mode: str = "record", encode=None, decode=None):
        """encode/decode rewrite the stored response text (the leak probe swaps refcodes for hashed ids)."""
        if mode not in MODES:
            raise ValueError(f"cassette mode must be one of {MODES}")
        self.path = Path(path) if path else None
        self.mode = "off" if self.path is None else mode
        self.entries: dict[str, dict] = {}
        self.hits = 0
        self.encode = encode or (lambda s: s)
        self.decode = decode or (lambda s: s)
        if self.mode != "off" and self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line after a crash
                self.entries.setdefault(e["key"], e)

    def lookup(self, request: dict) -> dict | None:
        if self.mode == "off":
            return None
        e = self.entries.get(request_key(request))
        if e is None:
            if self.mode == "replay":
                raise CassetteMiss(f"no cassette entry for request {request_key(request)[:12]} ({request_meta(request)})")
            return None
        self.hits += 1
        return json.loads(self.decode(json.dumps(e["response"], ensure_ascii=False)))

    def record(self, request: dict, response: dict) -> None:
        if self.mode != "record":
            return
        key = request_key(request)
        if key in self.entries:
            return
        stored = json.loads(self.encode(json.dumps(response, ensure_ascii=False)))
        entry = {"key": key, "request_meta": request_meta(request), "response": stored}
        self.entries[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
