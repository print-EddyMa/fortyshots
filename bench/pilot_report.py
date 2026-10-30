"""Where the time goes in recorded campaigns: per role, per round, per call.

usage: python -m bench.pilot_report runs/<id> [runs/<id> ...]
       python -m bench.pilot_report --ref origin/runs-mac --pool P0     (reads runs straight from a git ref)

Prints, for each run: wall time, model calls and their prefill/decode split per role, reasoning share of
output, schema failures and retries, errors, harness picks, tool use, hits. Used to tune plan-file knobs
(reasoning strength per role, Director call cap) on the dev pool P0 before the PREREG freeze.
"""
import argparse
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


def _git_runs(ref: str, pool: str | None) -> dict[str, list[dict]]:
    names = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "runs/"], capture_output=True, text=True,
                           check=True).stdout.split()
    out = {}
    for n in names:
        if not n.endswith("/events.jsonl"):
            continue
        run_id = n.split("/")[1]
        if pool and f"-{pool}-" not in run_id:
            continue
        text = subprocess.run(["git", "show", f"{ref}:{n}"], capture_output=True, text=True, check=True).stdout
        out[run_id] = [json.loads(line) for line in text.splitlines() if line.strip()]
    return out


def _dir_runs(paths: list[str]) -> dict[str, list[dict]]:
    out = {}
    for p in paths:
        f = Path(p) / "events.jsonl"
        out[Path(p).name] = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return out


def report(run_id: str, events: list[dict]) -> dict:
    calls = [e for e in events if e["kind"] == "llm_call"]
    by_role = defaultdict(lambda: Counter())
    for e in calls:
        t, tok = e.get("timings") or {}, e.get("tokens") or {}
        c = by_role[e["role"]]
        c["calls"] += 1
        c["prompt_new"] += t.get("prompt_n") or 0
        c["prompt_cached"] += t.get("cache_n") or 0
        c["generated"] += t.get("predicted_n") or 0
        c["reasoning"] += tok.get("reasoning") or 0
        c["prefill_s"] += (t.get("prompt_n") or 0) / max(t.get("prompt_per_second") or 1e9, 1e-9)
        c["decode_s"] += (t.get("predicted_n") or 0) / max(t.get("predicted_per_second") or 1e9, 1e-9)
        c["wall_s"] += (e.get("dur_ms") or 0) / 1000
        if e["payload"].get("finish_reason") == "length":
            c["length_cuts"] += 1
    tools = Counter(e["payload"]["name"] for e in events if e["kind"] == "tool_call")
    errors = Counter(e["payload"].get("where", "?") for e in events if e["kind"] == "error")
    summary = next((e["payload"] for e in reversed(events) if e["kind"] == "summary"), {})
    header = next((e["payload"] for e in events if e["kind"] == "header"), {})
    t0, t1 = events[0]["t"], events[-1]["t"]
    dec = [e["timings"]["predicted_per_second"] for e in calls if (e.get("timings") or {}).get("predicted_per_second")]
    pre = [e["timings"]["prompt_per_second"] for e in calls
           if (e.get("timings") or {}).get("prompt_per_second") and (e["timings"].get("prompt_n") or 0) > 200]
    return {"run_id": run_id, "start": t0, "end": t1, "duration_s": summary.get("duration_s"),
            "hits": summary.get("hits"), "spent": summary.get("spent"), "aborted": summary.get("aborted"),
            "harness_picks": summary.get("harness_picks"), "autofill": summary.get("autofill"),
            "config": header.get("config", {}), "model": header.get("model", {}),
            "decode_tps_median": statistics.median(dec) if dec else None,
            "prefill_tps_median": statistics.median(pre) if pre else None,
            "roles": {r: dict(c) for r, c in by_role.items()}, "tools": dict(tools), "errors": dict(errors),
            "n_calls": len(calls)}


def show(r: dict) -> None:
    m = (r["duration_s"] or 0) / 60
    print(f"\n{r['run_id']}  {m:.1f} min  hits {r['hits']}/{r['spent']}  aborted {r['aborted']}  "
          f"harness picks {r['harness_picks']}  calls {r['n_calls']}")
    print(f"  decode median {r['decode_tps_median']} tok/s, prefill median {r['prefill_tps_median']} tok/s;"
          f" strengths {r['config'].get('strengths')}; director cap {r['config'].get('max_director_calls')}")
    print(f"  {'role':9s} {'calls':>5s} {'new pr':>7s} {'cached':>7s} {'gen':>6s} {'reason':>6s} "
          f"{'prefill':>8s} {'decode':>7s} {'wall':>7s} {'cuts':>4s}")
    for role, c in sorted(r["roles"].items(), key=lambda kv: -kv[1].get("wall_s", 0)):
        print(f"  {role:9s} {c.get('calls', 0):5d} {c.get('prompt_new', 0):7d} {c.get('prompt_cached', 0):7d} "
              f"{c.get('generated', 0):6d} {c.get('reasoning', 0):6d} {c.get('prefill_s', 0) / 60:7.1f}m "
              f"{c.get('decode_s', 0) / 60:6.1f}m {c.get('wall_s', 0) / 60:6.1f}m {c.get('length_cuts', 0):4d}")
    print(f"  tools {r['tools']}")
    if r["errors"]:
        print(f"  errors {r['errors']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--ref")
    ap.add_argument("--pool")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    runs = _git_runs(a.ref, a.pool) if a.ref else _dir_runs(a.runs)
    reps = [report(k, v) for k, v in sorted(runs.items()) if v]
    if a.json:
        print(json.dumps(reps, indent=1))
    else:
        for r in reps:
            show(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
