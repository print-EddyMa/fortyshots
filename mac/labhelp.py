"""JSON helpers for ./lab. Standard library only; runs on macOS /usr/bin/python3 (3.9) before any setup.

Every subcommand prints plain text that bash can read line by line. Errors go to stderr with exit code 2.
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _die(msg):
    sys.stderr.write(f"labhelp: {msg}\n")
    sys.exit(2)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        _die(f"cannot read {path}: {e}")


def _profile(cfg, name):
    profiles = cfg.get("profiles", {})
    if name not in profiles:
        _die(f"unknown profile '{name}' (known: {', '.join(sorted(profiles))})")
    return profiles[name]


def _url(spec):
    return f"https://huggingface.co/{spec['repo']}/resolve/main/{spec['file']}"


def cmd_profiles(cfg_path):
    """name<TAB>description for every profile."""
    cfg = _load(cfg_path)
    for name, p in cfg["profiles"].items():
        print(f"{name}\t{p.get('description', '')}")


def cmd_profile(cfg_path, name, field):
    """One field of a profile. model/mmproj/draft print the file name ('' when null)."""
    cfg = _load(cfg_path)
    p = _profile(cfg, name)
    if field in ("model_sha256", "model_size"):
        print(cfg["files"][p["model"]][field.split("_", 1)[1]])
    elif field in ("model", "mmproj", "draft"):
        key = p.get(field)
        print(cfg["files"][key]["file"] if key else "")
    elif field == "ram_gb":
        print(cfg.get("ram_gb", 24))
    elif field == "wired_limit_mb":
        print(cfg.get("wired_limit_mb", 20480))
    else:
        v = p.get(field)
        print("" if v is None else v)


def cmd_files(cfg_path, *names):
    """file<TAB>size<TAB>sha256<TAB>url for the files the named profiles need ('all' = every file)."""
    cfg = _load(cfg_path)
    keys = []
    if not names or "all" in names:
        keys = list(cfg["files"])
    else:
        for n in names:
            p = _profile(cfg, n)
            for f in ("model", "mmproj", "draft"):
                if p.get(f) and p[f] not in keys:
                    keys.append(p[f])
    for k in keys:
        s = cfg["files"][k]
        print(f"{s['file']}\t{s['size']}\t{s['sha256']}\t{_url(s)}")


def cmd_server_args(cfg_path, name, model_dir, port):
    """llama-server arguments for a profile, one per line."""
    cfg = _load(cfg_path)
    p = _profile(cfg, name)
    files = cfg["files"]
    args = ["-m", os.path.join(model_dir, files[p["model"]]["file"])]
    if p.get("mmproj"):
        args += ["--mmproj", os.path.join(model_dir, files[p["mmproj"]]["file"])]
    if p.get("draft"):
        if float(p.get("headroom_gb", 0)) < 3:
            _die(f"profile {name} has a drafter but less than 3 GB headroom; refusing")
        args += ["-md", os.path.join(model_dir, files[p["draft"]]["file"])] + list(cfg.get("draft_flags", []))
    args += ["-c", str(p["ctx"])] + list(cfg["common_flags"]) + ["--port", str(port)]
    for a in args:
        print(a)


def _post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only, never a proxy
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def cmd_chat(base_url):
    """One chat call for the smoke test. Prints key=value lines."""
    if not base_url.startswith(("http://127.0.0.1", "http://localhost")):
        _die(f"refusing non-loopback url {base_url}")
    body = {
        "model": "muse-glimmer-30b",
        "messages": [{"role": "user", "content": "What is 17 * 23? Reply with just the number."}],
        "max_tokens": 1024,
        "chat_template_kwargs": {"reasoning_strength": "low"},
    }
    t0 = time.time()
    try:
        d = _post(base_url.rstrip("/") + "/v1/chat/completions", body, 600)
    except (urllib.error.URLError, OSError, ValueError) as e:
        _die(f"chat call failed: {e}")
    dt = time.time() - t0
    ch = (d.get("choices") or [{}])[0]
    m = ch.get("message") or {}
    t = d.get("timings") or {}
    content = (m.get("content") or "").strip().replace("\n", " ")
    print(f"content={content[:200]}")
    print(f"reasoning_chars={len(m.get('reasoning_content') or '')}")
    print(f"finish_reason={ch.get('finish_reason')}")
    print(f"seconds={dt:.1f}")
    for k in ("prompt_n", "prompt_per_second", "predicted_n", "predicted_per_second"):
        v = t.get(k)
        print(f"{k}={'' if v is None else (round(v, 2) if isinstance(v, float) else v)}")
    print(f"correct={'yes' if '391' in content else 'no'}")


_BENCH_ROWS = "\n".join(
    f"m{i:07x}  Zn{i % 3 + 1}C{8 + i % 9}H{6 + i % 7}O{4 + i % 5}  pld {3.1 + (i % 17) / 10:.1f}  lcd {4.2 + (i % 13) / 10:.1f}"
    f"  oms {'yes' if i % 4 == 0 else 'no'}  S_mace {-0.9 + (i % 23) / 20:.2f}"
    for i in range(0, 4000, 97))


def _bench_call(base_url, question):
    body = {
        "model": "muse-glimmer-30b",
        "messages": [
            {"role": "system", "content": "You are the director of a materials-discovery lab. Candidate MOFs:\n" + _BENCH_ROWS},
            {"role": "user", "content": question},
        ],
        "max_tokens": 900, "temperature": 0,
        "chat_template_kwargs": {"reasoning_strength": "low"},
    }
    t0 = time.time()
    d = _post(base_url.rstrip("/") + "/v1/chat/completions", body, 900)
    return d, time.time() - t0


def cmd_bench(base_url):
    """Decode and prefill speed on an agent-sized prompt: one cold call, then one that reuses the cached prefix."""
    if not base_url.startswith(("http://127.0.0.1", "http://localhost")):
        _die(f"refusing non-loopback url {base_url}")
    qs = ["Pick the 5 most promising MOFs for humid-air CO2 capture and give one sentence of reasoning for each.",
          "Now pick 5 different ones, avoiding any you already chose, again with one sentence each."]
    for tag, q in zip(("cold", "warm"), qs):
        try:
            d, dt = _bench_call(base_url, q)
        except (urllib.error.URLError, OSError, ValueError) as e:
            _die(f"bench call failed: {e}")
        t = d.get("timings") or {}
        ch = (d.get("choices") or [{}])[0]
        print(f"{tag}.seconds={dt:.1f}")
        print(f"{tag}.finish_reason={ch.get('finish_reason')}")
        for k, v in sorted(t.items()):
            if isinstance(v, (int, float)):
                print(f"{tag}.{k}={round(v, 3) if isinstance(v, float) else v}")


def _coerce(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def _set(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def cmd_write_json(out, *pairs):
    """write-json OUT key=value ... ; key@=path embeds a JSON file (or null if missing).
    Values are parsed as JSON when possible, otherwise kept as strings. Dotted keys nest."""
    doc = {}
    for pair in pairs:
        if "@=" in pair:
            k, path = pair.split("@=", 1)
            try:
                with open(path) as f:
                    _set(doc, k, json.load(f))
            except (OSError, ValueError):
                _set(doc, k, None)
        elif "=" in pair:
            k, v = pair.split("=", 1)
            _set(doc, k, _coerce(v))
        else:
            _die(f"bad pair {pair!r}")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def cmd_json_get(path, dotted):
    """Print one value from a JSON file ('' if missing)."""
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        print()
        return
    for k in dotted.split("."):
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            print()
            return
    print(json.dumps(d) if isinstance(d, (dict, list)) else ("" if d is None else d))


def cmd_plan(path):
    """arm<TAB>pool<TAB>seed for every run in a plan file (object with 'runs', or a bare list)."""
    d = _load(path)
    runs = d.get("runs") if isinstance(d, dict) else d
    if not isinstance(runs, list) or not runs:
        _die(f"{path} has no runs")
    for r in runs:
        print(f"{r['arm']}\t{r['pool']}\t{r['seed']}")


def _run_info(run_dir):
    rid = os.path.basename(run_dir.rstrip("/"))
    s_path = os.path.join(run_dir, "summary.json")
    e_path = os.path.join(run_dir, "events.jsonl")
    mtime = max([os.path.getmtime(p) for p in (s_path, e_path, run_dir) if os.path.exists(p)])
    info = {"run_id": rid, "mtime": mtime, "finished": os.path.exists(s_path)}
    if info["finished"]:
        try:
            with open(s_path) as f:
                s = json.load(f)
            info.update(hits=s.get("hits"), spent=s.get("spent"), duration_s=s.get("duration_s"),
                        aborted=s.get("aborted"))
        except (OSError, ValueError):
            pass
    return info


def cmd_runs(runs_dir, since="0"):
    """run_id<TAB>finished<TAB>hits<TAB>spent<TAB>minutes<TAB>age_min for run dirs changed after `since` (epoch),
    newest last."""
    since = float(since)
    rows = []
    for d in glob.glob(os.path.join(runs_dir, "*", "")):
        if not os.path.exists(os.path.join(d, "events.jsonl")):
            continue
        i = _run_info(d)
        if i["mtime"] >= since:
            rows.append(i)
    rows.sort(key=lambda r: r["mtime"])
    now = time.time()
    for r in rows:
        mins = "" if r.get("duration_s") is None else f"{r['duration_s'] / 60.0:.1f}"
        hits = "" if r.get("hits") is None else r["hits"]
        spent = "" if r.get("spent") is None else r["spent"]
        done = "yes" if r["finished"] else "no"
        print(f"{r['run_id']}\t{done}\t{hits}\t{spent}\t{mins}\t{int((now - r['mtime']) / 60)}")


def cmd_run_speed(run_dir):
    """Median decode and prefill tok/s over the llm_call events of one run: 'decode prefill n'."""
    dec, pre = [], []
    try:
        with open(os.path.join(run_dir, "events.jsonl")) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                t = e.get("timings") or {}
                if e.get("kind") == "llm_call" and t:
                    if t.get("predicted_per_second"):
                        dec.append(float(t["predicted_per_second"]))
                    if t.get("prompt_per_second"):
                        pre.append(float(t["prompt_per_second"]))
    except OSError:
        pass

    def med(xs):
        xs = sorted(xs)
        return "" if not xs else f"{xs[len(xs) // 2]:.1f}"

    print(f"{med(dec) or '-'} {med(pre) or '-'} {len(dec)}")


COMMANDS = {
    "profiles": cmd_profiles,
    "profile": cmd_profile,
    "files": cmd_files,
    "server-args": cmd_server_args,
    "chat": cmd_chat,
    "bench": cmd_bench,
    "write-json": cmd_write_json,
    "json-get": cmd_json_get,
    "plan": cmd_plan,
    "runs": cmd_runs,
    "run-speed": cmd_run_speed,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        _die(f"usage: labhelp.py {{{'|'.join(COMMANDS)}}} ...")
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except TypeError as e:
        _die(f"bad arguments for {sys.argv[1]}: {e}")
