"""Checks for the Mac runtime: profiles, plans, the JSON helper, shellcheck, and ./lab end to end (LAB_MOCK=1)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / "mac"
HELP = [sys.executable, str(MAC / "labhelp.py")]


def helper(*args):
    return subprocess.run([*HELP, *args], capture_output=True, text=True, check=False)


def test_profiles_are_consistent():
    cfg = json.loads((MAC / "profiles.json").read_text())
    assert set(cfg["profiles"]) >= {"default", "live", "small"}
    d = cfg["profiles"]["default"]
    assert (d["model"], d["mmproj"], d["draft"], d["ctx"]) == ("ud_q3kxl", "mmproj", "dflash", 32768)
    live = cfg["profiles"]["live"]
    assert (live["model"], live["mmproj"], live["ctx"]) == ("ud_q3kxl", "mmproj", 16384)
    assert cfg["profiles"]["small"]["model"] == "ud_q3kxl"
    for name, p in cfg["profiles"].items():
        for key in ("model", "mmproj", "draft"):
            assert p[key] is None or p[key] in cfg["files"], (name, key)
        assert abs(p["est_total_gb"] + p["headroom_gb"] - cfg["ram_gb"]) < 0.05, name
        if p["draft"]:
            assert p["headroom_gb"] >= 3, f"{name}: DFlash needs at least 3 GB headroom"
            assert not (p["model"] == "q4km" and p["mmproj"]), "never Q4_K_M + mmproj + dflash"
        assert p["fallback"] is None or p["fallback"] in cfg["profiles"]
    for f in cfg["files"].values():
        assert len(f["sha256"]) == 64 and f["size"] > 10**9
    assert "--jinja" in cfg["common_flags"] and "127.0.0.1" in cfg["common_flags"]


def test_server_args():
    out = helper("server-args", str(MAC / "profiles.json"), "default", "/models", "8080")
    assert out.returncode == 0, out.stderr
    args = out.stdout.split("\n")
    assert args[:2] == ["-m", "/models/Muse-Glimmer-30B-UD-Q3_K_XL.gguf"]
    assert "--mmproj" in args and "-md" in args
    assert args[args.index("-c") + 1] == "32768" and args[args.index("--port") + 1] == "8080"
    out = helper("server-args", str(MAC / "profiles.json"), "small-dflash", "/models", "8080")
    assert "-md" in out.stdout.split("\n")


def test_server_args_refuse_tight_drafter(tmp_path):
    cfg = json.loads((MAC / "profiles.json").read_text())
    cfg["profiles"]["bad"] = {**cfg["profiles"]["default"], "draft": "dflash", "headroom_gb": 2.9}
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps(cfg))
    out = helper("server-args", str(p), "bad", "/models", "8080")
    assert out.returncode != 0 and "headroom" in out.stderr


def test_plans():
    arms = {"A4", "A5", "A6", "L", "L2"}
    pools = {f"P{i}" for i in range(6)}
    plans = {p.stem: json.loads(p.read_text()) for p in (MAC / "plans").glob("*.json")}
    assert set(plans) >= {"pilot", "main", "second", "resume"}
    for name, plan in plans.items():
        runs = plan["runs"]
        assert runs, name
        for r in runs:
            assert r["arm"] in arms and r["pool"] in pools and isinstance(r["seed"], int), (name, r)
        keys = [(r["arm"], r["pool"], r["seed"]) for r in runs]
        assert len(keys) == len(set(keys)), f"{name} repeats a run"
        if name != "pilot":
            assert all(r["pool"] != "P0" for r in runs), f"{name} must not use the dev pool"
    assert {(r["arm"], r["pool"]) for r in plans["pilot"]["runs"]} == {("A5", "P0")}
    main = plans["main"]["runs"]
    assert [(r["arm"], r["pool"], r["seed"]) for r in main[:5]] == [("A5", f"P{i}", 0) for i in range(1, 6)]
    # every arm at seed 0 on all pools before any seed 1 (PREREG section 7)
    order = main + plans["second"]["runs"]
    first_seed1 = next(i for i, r in enumerate(order) if r["seed"] == 1)
    assert all(r["seed"] == 0 for r in order[:first_seed1])
    resume = {(r["arm"], r["pool"], r["seed"]) for r in plans["resume"]["runs"]}
    assert {(r["arm"], r["pool"], r["seed"]) for r in order} <= resume


def test_shellcheck():
    sc = shutil.which("shellcheck") or str(Path(sys.executable).parent / "shellcheck")
    if not Path(sc).exists() and not shutil.which(sc):
        pytest.skip("shellcheck not installed")
    out = subprocess.run([sc, "-s", "bash", str(ROOT / "lab"), str(ROOT / "tests" / "test_lab.sh")],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout


def test_lab_end_to_end_mock():
    if os.environ.get("FORTYSHOTS_SKIP_LAB_TEST"):
        pytest.skip("FORTYSHOTS_SKIP_LAB_TEST is set")
    if not shutil.which("bash") or not shutil.which("git") or not shutil.which("curl"):
        pytest.skip("needs bash, git and curl")
    env = dict(os.environ, LAB_VENV=sys.prefix)
    out = subprocess.run(["bash", str(ROOT / "tests" / "test_lab.sh")], env=env, capture_output=True, text=True,
                         timeout=1200, check=False)
    if out.returncode == 77:
        pytest.skip(out.stdout.strip())
    assert out.returncode == 0, out.stdout[-6000:] + out.stderr[-2000:]
