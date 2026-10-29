"""Read finished LLM campaigns from runs/<run_id>/{summary.json, events.jsonl}.

A run counts when summary.json exists. The purchase order is rebuilt from the ledger (the ids of the
`dft_result` events, each checked against its `dft_order`), never taken from summary.picks; hit flags are
recomputed from data/labels.parquet for those ids. The summary is only cross-checked: every disagreement
between summary, ledger, header, pool and labels is recorded in Run.mismatches (bench.verify fails on any).
Runs with mismatches, and runs whose header budget is not the pre-registered 40, are left out of the
analysis with the reason in Run.excluded. Aborted runs count with whatever they bought (intention to
treat). Runs whose id starts with "mock-" are skipped unless include_mock.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from bench.common import BUDGET

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
    excluded: str | None = None  # reason the run is left out of the analysis, if it is
    harness_picks: int = 0

    @property
    def harness_share(self):
        """Share of the purchases chosen by code rather than the model (None when nothing was bought)."""
        return min(self.harness_picks, len(self.picks)) / len(self.picks) if self.picks else None


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


def ledger_purchases(events, hit_of):
    """(purchase order, problems) from the dft_order / dft_result events, in ledger order."""
    order, problems, open_order = [], [], None
    for e in events:
        kind, pay = e.get("kind"), e.get("payload") or {}
        if kind == "dft_order":
            open_order = [str(i) for i in pay.get("ids") or []]
        elif kind == "dft_result":
            rows = pay.get("results") or []
            ids = [str(row.get("id")) for row in rows]
            if open_order is None:
                problems.append(f"dft_result at seq {e.get('seq')} has no preceding dft_order")
            elif not set(ids) <= set(open_order):
                problems.append(f"dft_result at seq {e.get('seq')} returns ids its dft_order did not request")
            for row, i in zip(rows, ids):
                if i in hit_of and row.get("hit") is not None and bool(row["hit"]) != hit_of[i]:
                    problems.append(f"dft_result hit flag for {i} differs from labels")
            order += ids
            open_order = None
    return order, problems


def _harness_picks(s):
    """summary.harness_picks (int), else len(summary.harness_pick_ids), else 0 (runs older than the key)."""
    if s.get("harness_picks") is not None:
        return int(s["harness_picks"])
    return len(s.get("harness_pick_ids") or [])


def load_run(run_dir, hit_of, pool_of=None):
    """pool_of: {id: pool} from data/universe.parquet; when given, every purchase must be in the run's pool."""
    run_dir = Path(run_dir)
    s = json.loads((run_dir / "summary.json").read_text())
    events = read_events(run_dir / "events.jsonl")
    arm, pool, seed = s.get("arm"), s.get("pool"), int(s.get("seed", 0))
    summary_picks = [str(i) for i in s.get("picks") or []]
    is_probe = arm in LEAK_ARMS
    mism = []

    header = next((e.get("payload") or {} for e in events if e.get("kind") == "header"), None)
    if header is None:
        mism.append("events.jsonl has no header event")
        header = {}
    for key, want in (("arm", arm), ("pool", pool), ("seed", seed)):
        got = header.get(key)
        if got is None or (int(got) if key == "seed" else got) != want:
            mism.append(f"header {key} {got!r} != summary {key} {want!r}")
    if s.get("run_id") is not None and s["run_id"] != run_dir.name:
        mism.append(f"summary run_id {s['run_id']!r} != run directory {run_dir.name!r}")
    final = [e.get("payload") for e in events if e.get("kind") == "summary"]
    if not final:
        mism.append("events.jsonl has no summary event")
    elif final[-1] != s:
        mism.append("summary.json differs from the final summary event")

    ledger, problems = ledger_purchases(events, hit_of)
    mism += problems
    if is_probe:
        # Leak probes buy nothing; their picks are the top of the ranking (scored from summary.leak / ranking).
        if ledger:
            mism.append(f"leak probe bought {len(ledger)} lookups")
        picks = summary_picks
    else:
        picks = ledger
        if ledger != summary_picks:
            mism.append(f"summary picks ({len(summary_picks)}) differ from the ledger purchase order "
                        f"({len(ledger)} dft_result ids)")
        budget = header.get("budget")
        if budget is None:
            mism.append("header has no budget")
        elif len(picks) > int(budget):
            mism.append(f"{len(picks)} purchases exceed the header budget {budget}")
        if s.get("spent") is not None and int(s["spent"]) != len(picks):
            mism.append(f"summary spent {s['spent']} != {len(picks)} purchases in the ledger")
    if len(set(picks)) != len(picks):
        mism.append(f"{len(picks) - len(set(picks))} duplicate purchase(s)")
    if pool_of is not None:
        outside = [i for i in picks if pool_of.get(i) != pool]
        if outside:
            mism.append(f"{len(outside)} pick(s) not in pool {pool}, e.g. {outside[0]}")

    flags = [bool(hit_of.get(i, False)) for i in picks]
    if s.get("hit_flags") is None or [bool(x) for x in s["hit_flags"]] != flags:
        mism.append("summary hit_flags differ from labels")
    if not is_probe and s.get("hits") is not None and int(s["hits"]) != sum(flags):
        mism.append(f"summary hits {s['hits']} != {sum(flags)} from labels")

    hp = _harness_picks(s)
    hp_ids = s.get("harness_pick_ids")
    if hp_ids is not None and (len(hp_ids) != hp or not set(map(str, hp_ids)) <= set(picks)):
        mism.append("summary harness_pick_ids disagree with harness_picks or with the purchases")

    p_by_id = dict(zip(summary_picks, list(s.get("p_hits") or [])))
    r = Run(run_id=run_dir.name, arm=arm, pool=pool, seed=seed, mock=run_dir.name.startswith("mock-"),
            picks=picks, hit_flags=flags, p_hits=[p_by_id.get(i) for i in picks],
            aborted=bool(s.get("aborted", False)), summary=s, header=header, mismatches=mism, harness_picks=hp)
    for e in events:
        kind, pay = e.get("kind"), e.get("payload") or {}
        key = (int(e.get("round") or 0), int(e.get("seq") or 0), pay)
        if kind == "calibration":
            r.calibrations.append(key)
        elif kind == "critique":
            r.critiques.append(key)
        elif kind == "claimcheck":
            r.claimchecks.append(pay)
    if mism:
        r.excluded = f"{len(mism)} mismatch(es): {mism[0]}"
    elif not is_probe and int(header.get("budget")) != BUDGET:
        r.excluded = f"header budget {header.get('budget')} is not the pre-registered {BUDGET}"
    return r


def load_runs(runs_dir, hit_of, include_mock=False, pool_of=None):
    runs_dir = Path(runs_dir)
    out = []
    if not runs_dir.exists():
        return out
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if not (d / "summary.json").exists():
            continue
        if d.name.startswith("mock-") and not include_mock:
            continue
        out.append(load_run(d, hit_of, pool_of))
    return out


def critic_pairs(run, vision=True):
    """(visual_rho, computed spearman) per critique, paired with the latest calibration of the same round.

    vision=True keeps only critiques made with the parity image (critique.vision is true); vision=False
    keeps the others (text-only critics, and older runs without the key)."""
    pairs = []
    for rnd, seq, pay in run.critiques:
        if (pay.get("vision") is True) != vision:
            continue
        rho = pay.get("visual_rho")
        cals = [c for c in run.calibrations if c[0] == rnd and c[1] < seq and c[2].get("spearman") is not None]
        if rho is not None and cals:
            pairs.append((float(rho), float(max(cals, key=lambda c: c[1])[2]["spearman"])))
    return pairs
