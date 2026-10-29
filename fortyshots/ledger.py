"""Append-only JSONL event ledger (one fsync per event), run summaries and runs/index.json."""
import json
import os
from datetime import UTC, datetime
from pathlib import Path

CHECKED_KINDS = {"tool_result", "dft_result", "dft_order", "proposal", "critique"}


class LedgerDivergence(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            break  # torn write at the crash point; everything after it is discarded
    return events


class Ledger:
    """On resume, re-emitted events are matched against the prior ledger instead of being written again."""

    def __init__(self, run_dir: Path, run_id: str):
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.path = self.run_dir / "events.jsonl"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.prior = read_events(self.path)
        if self.prior:
            self._rewrite_clean()
        self.seq = 0

    def _rewrite_clean(self):
        good = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.prior)
        if self.path.read_text() != good:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(good)
            os.replace(tmp, self.path)

    @property
    def replaying(self) -> bool:
        return self.seq < len(self.prior)

    def peek(self) -> dict | None:
        return self.prior[self.seq] if self.replaying else None

    def emit(self, kind: str, role: str, payload: dict, round: int, call_id: str | None = None,
             tokens: dict | None = None, timings: dict | None = None, dur_ms: int | None = None) -> dict:
        if self.replaying:
            ev = self.prior[self.seq]
            if (ev["kind"], ev["role"], ev.get("call_id")) != (kind, role, call_id) or (
                    kind in CHECKED_KINDS and _norm(ev["payload"]) != _norm(payload)):
                raise LedgerDivergence(f"resume diverged at seq {self.seq}: ledger has {ev['kind']}/{ev['role']}/"
                                       f"{ev.get('call_id')}, run produced {kind}/{role}/{call_id}")
            self.seq += 1
            return ev
        ev = {"seq": self.seq, "t": now_iso(), "run_id": self.run_id, "round": round, "role": role, "kind": kind,
              "payload": payload, "call_id": call_id, "tokens": tokens, "timings": timings, "dur_ms": dur_ms}
        with open(self.path, "a") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.seq += 1
        return ev

    def write_summary(self, summary: dict) -> None:
        tmp = self.run_dir / "summary.json.tmp"
        tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        os.replace(tmp, self.run_dir / "summary.json")


def _norm(x):
    return json.loads(json.dumps(x))


def run_info(run_dir: Path) -> dict | None:
    events = read_events(run_dir / "events.jsonl")
    if not events or events[0]["kind"] != "header":
        return None
    h = events[0]["payload"]
    summ_path = run_dir / "summary.json"
    s = json.loads(summ_path.read_text()) if summ_path.exists() else None
    return {"run_id": run_dir.name, "arm": h.get("arm"), "pool": h.get("pool"), "seed": h.get("seed"),
            "mode": h.get("mode"), "hits": s.get("hits") if s else None, "spent": s.get("spent") if s else None,
            "duration_s": s.get("duration_s") if s else None, "started": events[0]["t"],
            "model": (h.get("model") or {}).get("name"), "finished": s is not None}


def build_index(runs_dir: Path) -> list[dict]:
    runs_dir = Path(runs_dir)
    rows = []
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.exists() else []:
        info = run_info(d)
        if info and info.pop("finished"):
            rows.append(info)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "index.json").write_text(json.dumps(rows, indent=2))
    return rows
