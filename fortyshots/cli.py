"""fortyshots command line: mock-llm, doctor, run, record, serve, index, verify."""
import argparse
import json
import sys
import traceback
from pathlib import Path

from .config import ARMS, BATCH, DEFAULT_BASE_URL, DEFAULT_MAX_MINUTES, MODES, ROUNDS
from .ledger import build_index


def _run_args(p: argparse.ArgumentParser, single: bool):
    if single:
        p.add_argument("--arm", required=True, choices=ARMS)
        p.add_argument("--pool", required=True)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--resume", action="store_true", help="continue the latest unfinished run with these settings")
        p.add_argument("--run-id", help="explicit run id (resumes it if it exists)")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--mode", choices=MODES, default=None)
    p.add_argument("--action-mode", choices=["native", "schema"], default=None)
    p.add_argument("--max-minutes", type=float, default=None)
    p.add_argument("--rounds", type=int, default=None,
                   help="rounds of 10 lookups (default 4 = the 40-lookup benchmark; fewer for demos)")
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--no-vision", action="store_true", help="critic gets a text parity table instead of images")
    p.add_argument("--shared-tools", action="store_true",
                   help="send the tool list to the proposer too (tool_choice none); enable only if doctor says ok")
    p.add_argument("--cassette", choices=["off", "record", "replay"], default="record")
    p.add_argument("--cassette-from", default=None, help="cassette file to replay (with --cassette replay)")
    p.add_argument("--chemist", action="store_true", help="ask the Chemist for mutation specs after the campaign")
    p.add_argument("--doctor-report", default="doctor/report.json")


def _config(args, arm: str, pool: str, seed: int, overrides: dict | None = None):
    from .campaign import RunConfig
    from .config import data_dir

    o = overrides or {}
    mode = o.get("mode") or args.mode or "recorded"
    rounds = int(o.get("rounds") or args.rounds or ROUNDS)
    return RunConfig(
        arm=arm, pool=pool, seed=int(seed), mode=mode,
        action_mode=o.get("action_mode") or args.action_mode or "native",
        base_url=o.get("base_url") or args.base_url,
        max_minutes=float(o.get("max_minutes") or args.max_minutes or DEFAULT_MAX_MINUTES),
        runs_dir=Path(args.runs_dir), data_dir=data_dir(o.get("data_dir") or args.data_dir),
        vision=not (o.get("no_vision", args.no_vision)), shared_tools=bool(o.get("shared_tools", args.shared_tools)),
        cassette=args.cassette, cassette_from=Path(args.cassette_from) if args.cassette_from else None,
        chemist=bool(o.get("chemist", args.chemist)), rounds=rounds, budget=rounds * BATCH,
        doctor_report=Path(args.doctor_report) if args.doctor_report else None)


def _transport(cfg):
    """Mock runs answer in-process when no mock server is listening."""
    if cfg.mode != "mock":
        return None
    import httpx

    from .llm import assert_loopback
    try:
        httpx.get(assert_loopback(cfg.base_url) + "/health", timeout=1.0, trust_env=False).raise_for_status()
        return None
    except httpx.HTTPError:
        from . import mockllm
        print(f"no server at {cfg.base_url}; answering with the in-process mock", file=sys.stderr)
        return mockllm.transport()


def _campaign(cfg, run_id: str | None, resume: bool):
    from .campaign import Campaign, find_runs

    if run_id is None and resume:
        _, unfinished = find_runs(cfg)
        run_id = unfinished[-1].name if unfinished else None
    if run_id is None:
        from .campaign import new_run_id
        run_id = new_run_id(cfg)
        if (cfg.runs_dir / run_id).exists():
            raise SystemExit(f"{cfg.runs_dir / run_id} already exists; pass --resume or --run-id")
    return Campaign(cfg, run_id=run_id, transport=_transport(cfg))


def cmd_run(args):
    cfg = _config(args, args.arm, args.pool, args.seed)
    camp = _campaign(cfg, args.run_id, args.resume)
    print(f"{'resuming' if camp.resumed else 'starting'} {camp.run_id}", file=sys.stderr)
    summary = camp.run()
    build_index(cfg.runs_dir)
    print(json.dumps({k: summary[k] for k in ("run_id", "hits", "spent", "duration_s", "aborted", "autofill")}))


def cmd_record(args):
    from .campaign import find_runs

    plan = json.loads(Path(args.plan).read_text())
    defaults = plan.get("defaults", {}) if isinstance(plan, dict) else {}
    items = plan.get("runs", []) if isinstance(plan, dict) else plan
    failures = 0
    for item in items:
        o = {**defaults, **item}
        cfg = _config(args, o["arm"], o["pool"], o.get("seed", 0), o)
        finished, unfinished = find_runs(cfg)
        if finished:
            print(f"skip {finished[-1].name} (finished)", file=sys.stderr)
            continue
        try:
            camp = _campaign(cfg, unfinished[-1].name if unfinished else None, False)
            print(f"{'resume' if camp.resumed else 'start'} {camp.run_id}", file=sys.stderr)
            s = camp.run()
            print(json.dumps({k: s[k] for k in ("run_id", "hits", "spent", "duration_s", "aborted")}), flush=True)
        except Exception:  # noqa: BLE001 - one failed campaign must not stop the night's plan
            failures += 1
            traceback.print_exc()
        build_index(cfg.runs_dir)
    sys.exit(1 if failures else 0)


def cmd_mock(args):
    from .mockllm import serve

    serve(args.host, args.port)


def cmd_doctor(args):
    from .config import data_dir
    from .doctor import run_doctor

    rep = run_doctor(args.base_url, Path(args.out), data_dir(args.data_dir), args.pool, hash_gguf=args.hash_gguf)
    print(json.dumps({"ok": rep["ok"], "summary": rep["summary"]}, indent=2))
    sys.exit(0 if rep["ok"] else 1)


def cmd_serve(args):
    from .server import serve

    serve(Path(args.runs_dir), Path(args.site) if args.site else None, args.port)


def cmd_index(args):
    rows = build_index(Path(args.runs_dir))
    print(f"{len(rows)} finished runs indexed in {Path(args.runs_dir) / 'index.json'}")


def cmd_verify(args):
    try:
        from bench.verify import main as verify_main
    except ImportError:
        print("fortyshots verify needs bench/verify.py (bench.verify:main), which is not installed yet.",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(verify_main(args.rest) or 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fortyshots", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("mock-llm", help="deterministic fake llama-server")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.set_defaults(fn=cmd_mock)
    p = sub.add_parser("doctor", help="model round trips and throughput")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--out", default="doctor/report.json")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--pool", default="P0")
    p.add_argument("--hash-gguf", default=None, help="path of the GGUF to sha256 into the report")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("run", help="one campaign")
    _run_args(p, single=True)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("record", help="run a plan of campaigns; skips finished, resumes unfinished")
    p.add_argument("plan")
    _run_args(p, single=False)
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("serve", help="live mode web server")
    p.add_argument("--port", type=int, default=7777)
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--site", default="web/dist")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("index", help="rebuild runs/index.json")
    p.add_argument("--runs-dir", default="runs")
    p.set_defaults(fn=cmd_index)
    p = sub.add_parser("verify", help="recompute results/results.json from runs/ and data/")
    p.add_argument("rest", nargs=argparse.REMAINDER, help="passed through to bench.verify")
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
