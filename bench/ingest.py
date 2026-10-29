"""Read finished LLM campaigns from runs/<run_id>/{summary.json, events.jsonl}.

A run counts when summary.json exists. Hit flags are recomputed from data/labels.parquet for the
purchased ids (the summary's own flags are only cross-checked). Aborted runs count with whatever they
bought (intention to treat). Runs whose id starts with "mock-" are skipped unless include_mock.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

LEAK_ARMS = ("L", "L2")


@dataclass
class Run:
    run_id: str
    arm: str
    pool: str
    seed: int
    mock: bool
    picks: list
    hit_flags: list
    p_hits: list
    aborted: bool
    summary: dict
    header: dict = field(default_factory=dict)
    calibrations: list = field(default_factory=list)  # (round, seq, payload)
    critiques: list = field(default_factory=list)  # (round, seq, payload)
    claimchecks: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)


def read_events(path):
    if not path.exists():
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return sorted(out, key=lambda e: e.get("seq", 0))


def load_run(run_dir, hit_of):
    s = json.loads((run_dir / "summary.json").read_text())
    picks = [str(i) for i in s.get("picks", [])]
    flags = [bool(hit_of.get(i, False)) for i in picks]
    p_hits = list(s.get("p_hits") or [None] * len(picks))
    p_hits += [None] * (len(picks) - len(p_hits))
    r = Run(run_id=run_dir.name, arm=s["arm"], pool=s["pool"], seed=int(s.get("seed", 0)),
            mock=run_dir.name.startswith("mock-"), picks=picks, hit_flags=flags, p_hits=p_hits[:len(picks)],
            aborted=bool(s.get("aborted", False)), summary=s)
    if s.get("hit_flags") is not None and [bool(x) for x in s["hit_flags"]] != flags:
        r.mismatches.append("summary hit_flags differ from labels")
    for e in read_events(run_dir / "events.jsonl"):
        kind, pay = e.get("kind"), e.get("payload") or {}
        key = (int(e.get("round") or 0), int(e.get("seq") or 0), pay)
        if kind == "header":
            r.header = pay
        elif kind == "calibration":
            r.calibrations.append(key)
        elif kind == "critique":
            r.critiques.append(key)
        elif kind == "claimcheck":
            r.claimchecks.append(pay)
    return r


def load_runs(runs_dir, hit_of, include_mock=False):
    runs_dir = Path(runs_dir)
    out = []
    if not runs_dir.exists():
        return out
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if not (d / "summary.json").exists():
            continue
        if d.name.startswith("mock-") and not include_mock:
            continue
        out.append(load_run(d, hit_of))
    return out


def critic_pairs(run):
    """(visual_rho, computed spearman) per critique, paired with the latest calibration of the same round."""
    pairs = []
    for rnd, seq, pay in run.critiques:
        rho = pay.get("visual_rho")
        cals = [c for c in run.calibrations if c[0] == rnd and c[1] < seq and c[2].get("spearman") is not None]
        if rho is not None and cals:
            pairs.append((float(rho), float(max(cals, key=lambda c: c[1])[2]["spearman"])))
    return pairs
