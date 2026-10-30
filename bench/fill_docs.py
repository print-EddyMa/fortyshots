"""Fill the {{...}} placeholders in the docs from measured data, so no published number is typed by hand.

usage: python -m bench.fill_docs [--check] [--write [--allow-mock]] [--root .] [--ref origin/runs-mac]

Sources, all read-only: results/results.json (bench.results), runs/<id>/{summary.json, events.jsonl}, the Mac
reports doctor/*.json (local doctor/ first, then the git ref --ref, read with `git show`; run
`git fetch origin runs-mac` beforehand to see the latest), mac/profiles.json, web/site.config.json and git
(tags hackathon-start and prereg-v1, commit counts, HEAD).

--check (the default) prints `| placeholder | value or MISSING | source |` for every placeholder found in the
docs and exits 1 while any measurable placeholder is MISSING or a placeholder name is unknown. Placeholders that
only a person can fill (URLs, team name, hashtag, ...) are listed as "needs a human" and never invented.

--write substitutes every filled value in place (missing ones stay as {{...}}) and writes
results/doc_values.json with every value and its source. It refuses (exit 2) when results.json is missing, does
not match the schema, or has status "mock" (unless --allow-mock).
"""
import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bench import schema
from bench.common import BUDGET, LLM_ARMS, PREREG_TAG, ROOT, TEST_POOLS, _git, load_data
from bench.ingest import LEAK_ARMS, load_runs, read_events

DOCS = ["README.md", "SUBMISSION.md", "LAUNCH.md", "VIDEO.md", "MAC_SETUP.md"]
PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")
START_TAG = "hackathon-start"
DEFAULT_REF = "origin/runs-mac"
INSTRUCTIONAL = {"..."}  # `{{...}}` in prose means "every placeholder"; it is not one itself

# Never invented: only a person knows these.
HUMAN = {
    "VIDEO_URL": "the unlisted video link, after upload",
    "X_THREAD_URL": "the X thread link, after posting",
    "TEAM_NAME": "the team name on the Cerebral Valley form",
    "CV_GALLERY_URL": "the Cerebral Valley gallery link, once published",
    "EVENT_HASHTAG": "the event's official hashtag",
    "COVER_IMAGE_PATH": "the cover image chosen for the form (`npm run og` renders web/public/og.png from the "
                        "showcase run; a person checks it)",
}

ARM_NAMES = {"A0": "random", "A1": "heuristic", "A2": "MACE-greedy", "A3": "RF-UCB", "A4": "zero-shot",
             "A5": "agent", "A6": "agent without surrogate"}
ARM_KEYS = ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]
# Comparison placeholders -> the pre-registered test (results.json tests[].name contains "A5 - <arm>").
VS_TESTS = {"VS_RANDOM": "A0", "VS_A2": "A2", "VS_RF": "A3"}
DIFF_TESTS = {"A5_A2": "A2", "A5_A3": "A3"}
VERB = {"beats": "beats", "ties": "ties", "loses": "loses to"}
CHEAP_SORTS = {"S_mace", "E_CO2_mace"}
SORT_WORDS = {
    "S_mace": "the cheap MACE score (S_mace)", "E_CO2_mace": "MACE CO2 binding (E_CO2_mace)",
    "pld_A": "pore-limiting diameter (pld_A)", "lcd_A": "largest cavity diameter (lcd_A)",
    "density_gcm3": "density (density_gcm3)", "void_fraction": "void fraction (void_fraction)",
    "asa_m2g": "surface area (asa_m2g)",
}
SURROGATE_WORDS = "the random-forest surrogate fitted on the labels it bought (fit_surrogate)"
SCHEMA_WHERE = {"director.schema", "proposer.batch", "critic.review"}
ALPHA = 0.05


@dataclass
class Value:
    value: str | None
    source: str
    human: bool = False


class Missing(Exception):
    """Raised by a value rule when its data is absent; the message is the reason shown in --check."""


# ---- formatting -------------------------------------------------------------------------------

def f2(x):
    return f"{float(x):.2f}"


def fsigned(x):
    return f"{float(x):+.2f}"


def fp(p):
    p = float(p)
    return f"{p:.4f}" if p < 0.001 else f"{p:.3f}"


def f1(x):
    return f"{float(x):.1f}"


def fmin(x):
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}"


def need(x, what):
    if x is None:
        raise Missing(f"{what} is null")
    return x


# ---- comparison rule ----------------------------------------------------------------------------

def compare_word(test):
    """Pre-registered wording for A5 vs a baseline: "beats" when the one-sided p < 0.05 and the difference is
    positive; "loses" when the whole 95% CI is below 0; "ties" otherwise (the CI includes 0, or a CI above 0
    without a significant test)."""
    diff, hi, p = float(test["diff"]), float(test["ci"][1]), float(test["p"])
    if p < ALPHA and diff > 0:
        return "beats"
    if hi < 0:
        return "loses"
    return "ties"


# ---- trace rule (showcase run, N_CHANGED) -------------------------------------------------------

def _ranking_action(e):
    """('query_pool', sort_by) or ('fit_surrogate', None) for a model's ranking tool call, else None."""
    if e.get("kind") != "tool_call" or e.get("role") not in ("director", "proposer"):
        return None
    pay = e.get("payload") or {}
    args = pay.get("args")
    args = args if isinstance(args, dict) else {}
    if pay.get("name") == "query_pool":
        return ("query_pool", args.get("sort_by") or "S_mace")
    if pay.get("name") == "fit_surrogate":
        return ("fit_surrogate", None)
    return None


def describe(action):
    if action[0] == "fit_surrogate":
        return SURROGATE_WORDS
    return SORT_WORDS.get(action[1], f"`{action[1]}`")


def course_after_calibration(events):
    """What the model ranked by just before and just after its first calibrate_oracle result.

    Returns {calibrated, n, spearman, round, before, after, changed}: before is the last ranking tool call
    (query_pool sort_by, or fit_surrogate) by the Director/Proposer before the first calibration event, after is
    the first one following it; changed is before != after (None when either is missing)."""
    events = sorted(events, key=lambda e: e.get("seq", 0))
    cal = next((e for e in events if e.get("kind") == "calibration"), None)
    if cal is None:
        return {"calibrated": False, "before": None, "after": None, "changed": None}
    seq = cal.get("seq", 0)
    acts = [(e.get("seq", 0), _ranking_action(e)) for e in events]
    before = [a for s, a in acts if a and s < seq]
    after = [a for s, a in acts if a and s > seq]
    b, a = (before[-1] if before else None), (after[0] if after else None)
    pay = cal.get("payload") or {}
    return {"calibrated": True, "n": pay.get("n"), "spearman": pay.get("spearman"), "round": cal.get("round"),
            "before": b, "after": a, "changed": None if b is None or a is None else b != a}


def pick_showcase(runs):
    """PREREG 9: the A5 run on P1 with the median hits@40; ties go to fewer lookups to first hit, then the
    lowest seed. With an even number of runs the median lies between two counts; the closest runs tie."""
    cand = [r for r in runs if r.arm == "A5" and r.pool == "P1"]
    if not cand:
        return None
    hits = {r.run_id: sum(r.hit_flags[:BUDGET]) for r in cand}
    med = statistics.median(hits.values())

    def first(r):
        return next((k + 1 for k, f in enumerate(r.hit_flags[:BUDGET]) if f), BUDGET + 1)
    return min(cand, key=lambda r: (abs(hits[r.run_id] - med), first(r), r.seed, r.run_id))


# ---- inputs -------------------------------------------------------------------------------------

def git(root, *args):
    out = _git(root, *args)
    return out.stdout.strip() if out is not None and out.returncode == 0 else None


def load_reports(root, ref):
    """{name: report} for doctor/*.json: local files first, then those on git ref `ref` not present locally.
    Mock reports (mock: true, or a mock server command) are dropped."""
    out = {}
    local = Path(root) / "doctor"
    if local.is_dir():
        for p in sorted(local.glob("*.json")):
            try:
                out[p.name] = (json.loads(p.read_text()), f"doctor/{p.name}")
            except (OSError, json.JSONDecodeError):
                continue
    names = git(root, "ls-tree", "-r", "--name-only", ref, "doctor/") if ref else None
    for n in (names or "").split():
        name = n.split("/", 1)[1] if "/" in n else n
        if not n.endswith(".json") or "/" in name or name in out:
            continue
        text = git(root, "show", f"{ref}:{n}")
        try:
            out[name] = (json.loads(text), f"{ref}:{n}")
        except (TypeError, json.JSONDecodeError):
            continue
    return {k: v for k, v in out.items()
            if v[0].get("mock") is not True and "mock-llm" not in str(v[0].get("server_cmd") or "")}


def recorded_profile(root):
    """{name, quant, draft, vision} of the profile used for recorded campaigns (mac/profiles.json default)."""
    path = Path(root) / "mac" / "profiles.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    prof = (doc.get("profiles") or {}).get("default")
    if not prof:
        return None
    draft = (doc.get("files") or {}).get(prof.get("draft") or "", {}).get("file") or ""
    return {"name": "default", "quant": prof.get("quant"), "draft": draft, "vision": bool(prof.get("mmproj"))}


def bench_reports(reports, quant, draft, vision):
    """Bench reports (kind bench, started) for a quant / draft file / vision setup, newest first."""
    hits = []
    for name, (rep, src) in reports.items():
        if rep.get("kind") != "bench" or not rep.get("started") or not (rep.get("bench") or {}).get("cold"):
            continue
        has_vision = "--mmproj" in str(rep.get("server_cmd") or "")
        if rep.get("quant") == quant and (rep.get("draft") or "") == draft and has_vision == vision:
            hits.append((str(rep.get("ts") or name), rep, src))
    return [(rep, src) for _, rep, src in sorted(hits, key=lambda t: t[0], reverse=True)]


def latest(reports, kinds):
    rows = [(str(rep.get("ts") or name), rep, src) for name, (rep, src) in reports.items() if rep.get("kind") in kinds]
    return [(rep, src) for _, rep, src in sorted(rows, key=lambda t: t[0], reverse=True)]


class Context:
    def __init__(self, root, ref=DEFAULT_REF):
        self.root = Path(root)
        self.ref = ref
        path = self.root / "results" / "results.json"
        self.results = json.loads(path.read_text()) if path.exists() else None
        self.schema_errors = schema.validate_results(self.results) if self.results else []
        self.status = (self.results or {}).get("status")
        self.include_mock = self.status == "mock"
        self.pools = [p["pool"] for p in (self.results or {}).get("pools", [])] or list(TEST_POOLS)
        self.arms = {a["arm"]: a for a in (self.results or {}).get("arms", [])}
        self.tests = (self.results or {}).get("tests", [])
        self.reports = load_reports(self.root, ref)
        self.profile = recorded_profile(self.root)
        self._events = {}
        self.runs = self._analysed_runs()
        self.recorded = self._recorded_runs()

    def events(self, run_id):
        if run_id not in self._events:
            self._events[run_id] = read_events(self.root / "runs" / run_id / "events.jsonl")
        return self._events[run_id]

    def _analysed_runs(self):
        """The LLM runs bench.results analyses: not excluded, mock only when results.json is mock."""
        if not (self.root / "runs").is_dir() or not (self.root / "data" / "labels.parquet").exists():
            return []
        data = load_data(self.root / "data")
        hit_of = dict(zip(data.labels.id, data.labels.hit.astype(bool)))
        pool_of = dict(zip(data.universe.id, data.universe.pool))
        runs = load_runs(self.root / "runs", hit_of, self.include_mock, pool_of)
        return [r for r in runs if r.excluded is None
                and ((r.arm in LLM_ARMS and r.pool in self.pools) or r.arm in LEAK_ARMS)]

    def _recorded_runs(self):
        """[(run_id, header, summary)] for finished runs recorded on the Mac (header mode "recorded", not mock)."""
        out = []
        d = self.root / "runs"
        if not d.is_dir():
            return out
        for p in sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith("mock-")):
            if not (p / "summary.json").exists():
                continue
            header = next((e.get("payload") or {} for e in self.events(p.name) if e.get("kind") == "header"), {})
            if header.get("mode") == "recorded":
                out.append((p.name, header, json.loads((p / "summary.json").read_text())))
        return out

    def hw_runs(self):
        """Recorded runs on the test pools when there are any, else every recorded run (dev pool P0 pilots)."""
        test = [r for r in self.recorded if r[2].get("pool") in TEST_POOLS]
        return (test, "test-pool") if test else (self.recorded, "all")

    def test(self, other):
        t = next((t for t in self.tests if f"A5 - {other}" in t["name"]), None)
        if t is None:
            raise Missing(f"results.json has no A5 - {other} test (no analysed A5 runs)")
        return t

    def arm(self, a):
        if self.results is None:
            raise Missing("results/results.json not found")
        if a not in self.arms:
            raise Missing(f"results.json has no {a} arm (no analysed {a} runs)")
        return self.arms[a]


# ---- value rules ----------------------------------------------------------------------------------

def arm_values(ctx):
    out = {}
    for a in ARM_KEYS:
        src = f"results.json arms[{a} {ARM_NAMES[a]}]"
        out[f"{a}_N"] = (lambda a=a: str(ctx.arm(a)["n_runs"]), f"{src}.n_runs")
        out[f"{a}_HITS"] = (lambda a=a: f2(ctx.arm(a)["hits_mean"]), f"{src}.hits_mean")
        out[f"{a}_LO"] = (lambda a=a: f2(ctx.arm(a)["hits_ci"][0]), f"{src}.hits_ci[0]")
        out[f"{a}_HI"] = (lambda a=a: f2(ctx.arm(a)["hits_ci"][1]), f"{src}.hits_ci[1]")
        out[f"{a}_ENR"] = (lambda a=a: f2(need(ctx.arm(a)["enrichment"], f"{a} enrichment")) + "×",
                           f"{src}.enrichment (PREREG 6.2, per-pool ratio mean)")
        out[f"{a}_LPH"] = (lambda a=a: f1(need(ctx.arm(a)["lookups_per_hit"], f"{a} lookups_per_hit")),
                           f"{src}.lookups_per_hit")
    return out


def test_values(ctx):
    out = {}
    for key, other in DIFF_TESTS.items():
        out[f"{key}_DIFF"] = (lambda o=other: fsigned(ctx.test(o)["diff"]), f"results.json tests[A5 - {other}].diff")
        out[f"{key}_P"] = (lambda o=other: fp(ctx.test(o)["p"]), f"results.json tests[A5 - {other}].p (one-sided)")
    for key, other in VS_TESTS.items():
        out[key] = (lambda o=other: compare_word(ctx.test(o)),
                    (f"results.json tests[A5 - {other} {ARM_NAMES[other]}]: beats if p < 0.05 and diff > 0, "
                     f"loses if CI < 0, else ties"))
    return out


def headline(ctx, short=False):
    a5, a0, a2, a3 = (ctx.arm(a) for a in ("A5", "A0", "A2", "A3"))
    w0, w2, w3 = (compare_word(ctx.test(o)) for o in ("A0", "A2", "A3"))
    t2 = ctx.test("A2")
    h, lo, hi = f2(a5["hits_mean"]), f2(a5["hits_ci"][0]), f2(a5["hits_ci"][1])
    if short:
        return (f"the agent picked {h} hits in 40 lookups (95% CI {lo}–{hi}) vs {f2(a0['hits_mean'])} for random "
                f"and {f2(a2['hits_mean'])} for MACE-greedy, so it {VERB[w0]} random and {VERB[w2]} the strongest "
                f"baseline")
    if w2 == "beats":  # H1 holds (PREREG 9): A5 with CI and enrichment, both baselines named
        enr = a5.get("enrichment")
        return (f"The Glimmer agent (A5) picked {h} hits in 40 lookups (95% CI {lo}–{hi}"
                + (f"; {f2(enr)}× the random rate" if enr is not None else "")
                + f"), beating the strongest baseline, greedy on the cheap simulator (MACE-greedy, "
                f"{f2(a2['hits_mean'])}; one-sided p = {fp(t2['p'])}), and random ({f2(a0['hits_mean'])}); "
                f"the random forest (RF-UCB) picked {f2(a3['hits_mean'])}.")
    return (f"The Glimmer agent (A5) picked {h} hits in 40 lookups (95% CI {lo}–{hi}) against "
            f"{f2(a0['hits_mean'])} for random and {f2(a2['hits_mean'])} for the strongest baseline, greedy on the "
            f"cheap simulator (MACE-greedy): it {VERB[w0]} random, {VERB[w2]} MACE-greedy (difference "
            f"{fsigned(t2['diff'])}, p = {fp(t2['p'])}) and {VERB[w3]} the random forest (RF-UCB, "
            f"{f2(a3['hits_mean'])}).")


def win_tie_loss(ctx):
    w2, w3 = compare_word(ctx.test("A2")), compare_word(ctx.test("A3"))
    return (f"It {VERB[w3]} the random forest and {VERB[w2]} MACE-greedy ({f2(ctx.arm('A2')['hits_mean'])}), "
            f"the strongest baseline.")


def leak_sentence(ctx):
    """Leak AUC against the chance band of the probe's pool: 0.5 ± 1.96·sd of a Mann-Whitney AUC under the
    null, sd = sqrt((n1 + n0 + 1) / (12 n1 n0))."""
    leak = need((ctx.results or {}).get("leak"), "results.json leak")
    auc = need(leak.get("L_auc"), "leak.L_auc")
    pool = next((r.pool for r in ctx.runs if r.arm == "L"), "P1")
    row = next((p for p in ctx.results["pools"] if p["pool"] == pool), None)
    if row is None:
        raise Missing(f"leak probe pool {pool} not in results.json pools")
    n1, n0 = row["hits"], row["n"] - row["hits"]
    sd = ((n1 + n0 + 1) / (12 * n1 * n0)) ** 0.5
    lo, hi = 0.5 - 1.96 * sd, 0.5 + 1.96 * sd
    band = f"the range chance gives for {n1} hits in {row['n']} ({f2(lo)}–{f2(hi)})"
    base = f"that ranking the raw refcodes alone gives AUC {f2(auc)}, "
    if auc > hi:
        return base + f"above {band}, so the model may recall some published results"
    if auc < lo:
        return base + f"below {band}, so it gives no sign that the model recalls the answers"
    return base + f"inside {band}, so it gives no sign that the model recalls the answers"


def prereg_sha256(ctx):
    commit = git(ctx.root, "rev-parse", "--verify", "--quiet", f"{PREREG_TAG}^{{commit}}")
    if not commit:
        raise Missing(f"tag {PREREG_TAG} not found (PREREG not frozen)")
    if (ctx.results or {}).get("prereg_sha") != commit:
        raise Missing(f"results.json prereg_sha {(ctx.results or {}).get('prereg_sha')!r} is not the {PREREG_TAG} "
                      f"commit {commit[:12]}; rerun python -m bench.results")
    try:  # bytes, not text: the hash must equal attest.prereg_sha of the file itself
        out = subprocess.run(["git", "-C", str(ctx.root), "show", f"{PREREG_TAG}:PREREG.md"], check=False,
                             capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        out = None
    if out is None or out.returncode != 0:
        raise Missing(f"PREREG.md not found at {PREREG_TAG}")
    return hashlib.sha256(out.stdout).hexdigest()


def need_tag(ctx, tag):
    if not git(ctx.root, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"):
        raise Missing(f"tag {tag} not found")


def n_commits(ctx):
    need_tag(ctx, START_TAG)
    return need(git(ctx.root, "rev-list", "--count", f"{START_TAG}..HEAD"), "git rev-list")


def runs_range(ctx):
    shas = (git(ctx.root, "log", "--reverse", "--format=%h", "--", "runs/") or "").split()
    if not shas:
        raise Missing("no commit touches runs/ yet")
    return shas[0] if len(shas) == 1 else f"{shas[0]}..{shas[-1]}"


def site_config(ctx, key):
    path = ctx.root / "web" / "site.config.json"
    if not path.exists():
        raise Missing("web/site.config.json not found")
    return need(json.loads(path.read_text()).get(key), f"site.config.json {key}")


def ceiling_mean(ctx, key):
    rows = need((ctx.results or {}).get("ceilings"), "results.json ceilings")
    if not rows:
        raise Missing("results.json ceilings is empty")
    return f2(statistics.fmean(r[key] for r in rows))


def llm_timings(ctx, field, min_prompt=0):
    runs, which = ctx.hw_runs()
    vals = []
    for run_id, _, _ in runs:
        for e in ctx.events(run_id):
            t = e.get("timings") or {}
            if e.get("kind") == "llm_call" and t.get(field) and (t.get("prompt_n") or 0) >= min_prompt:
                vals.append(float(t[field]))
    return vals, len(runs), which


def bench_fallback(ctx, field, what):
    prof = ctx.profile
    if not prof:
        raise Missing(f"no recorded runs, and no mac/profiles.json default profile to pick a bench report for {what}")
    reps = bench_reports(ctx.reports, prof["quant"], prof["draft"], prof["vision"])
    if not reps:
        raise Missing(f"no recorded runs and no bench report for the default profile ({prof['quant']}, "
                      f"draft {prof['draft'] or 'none'}, vision {prof['vision']}) on {ctx.ref}")
    rep, src = reps[0]
    return rep["bench"]["cold"][field], src


def toks(ctx):
    vals, n, which = llm_timings(ctx, "predicted_per_second")
    if vals:
        return f1(statistics.median(vals)), (f"median timings.predicted_per_second over {len(vals)} llm_call events "
                                             f"in {n} recorded runs ({which} pools)")
    v, src = bench_fallback(ctx, "predicted_per_second", "TOKS")
    return f1(v), f"fallback, no recorded runs: {src} bench.cold.predicted_per_second"


def prefill_toks(ctx):
    vals, n, which = llm_timings(ctx, "prompt_per_second", min_prompt=200)
    if vals:
        return f"{statistics.median(vals):.0f}", (f"median timings.prompt_per_second over {len(vals)} llm_call events "
                                                  f"with prompt_n >= 200 in {n} recorded runs ({which} pools)")
    v, src = bench_fallback(ctx, "prompt_per_second", "PREFILL_TOKS")
    return f"{v:.0f}", f"fallback, no recorded runs: {src} bench.cold.prompt_per_second (cold prefill)"


def campaign_min(ctx):
    runs, which = ctx.hw_runs()
    mins = [s["duration_s"] / 60 for _, h, s in runs if h.get("arm") == "A5" and s.get("duration_s") is not None]
    if not mins:
        raise Missing("no recorded A5 run yet (the doctor's estimate is not a measurement)")
    return fmin(statistics.median(mins)), f"median summary.duration_s / 60 over {len(mins)} recorded A5 runs ({which} pools)"


def dflash(ctx):
    """(base report, drafter report) for the recorded profile's quant and vision setup, newest of each."""
    prof = need(ctx.profile, "mac/profiles.json default profile")
    if not prof["draft"]:
        raise Missing("the default profile has no drafter")
    with_d = bench_reports(ctx.reports, prof["quant"], prof["draft"], prof["vision"])
    without = bench_reports(ctx.reports, prof["quant"], "", prof["vision"])
    if not with_d or not without:
        raise Missing(f"need bench reports for {prof['quant']}{' + vision' if prof['vision'] else ''} with and "
                      f"without the drafter on {ctx.ref}")
    (b, bsrc), (d, dsrc) = without[0], with_d[0]
    cold_b, cold_d = b["bench"]["cold"], d["bench"]["cold"]
    acc = (cold_d["draft_n_accepted"] / cold_d["draft_n"]) if cold_d.get("draft_n") else None
    setup = f"{prof['quant']}{' + vision' if prof['vision'] else ''}"
    src = (f"{bsrc} vs {dsrc}: bench.cold.predicted_per_second, acceptance draft_n_accepted / draft_n "
           f"({cold_d.get('prompt_n')}-token prompt)")
    return f1(cold_b["predicted_per_second"]), f1(cold_d["predicted_per_second"]), acc, setup, src


def dflash_result(ctx):
    a, b, acc, setup, src = dflash(ctx)
    tail = f", {acc:.0%} of drafted tokens accepted" if acc is not None else ""
    return f"{a} → {b} tok/s with Meta's DFlash drafter{tail}", src + f" [{setup}]"


def dflash_sentence(ctx):
    a, b, acc, setup, src = dflash(ctx)
    tail = f", with {acc:.0%} of drafted tokens accepted" if acc is not None else ""
    return (f"Measured with `./lab bench` on this Mac, Meta's DFlash drafter takes {setup} decode from {a} to "
            f"{b} tok/s{tail}."), src


def from_headers_or_reports(ctx, header_get, report_get, what):
    vals = Counter(v for v in (header_get(h) for _, h, _ in ctx.recorded) if v)
    if vals:
        return ", ".join(v for v, _ in vals.most_common()), f"recorded run headers ({sum(vals.values())} runs): {what}"
    for rep, src in latest(ctx.reports, {"doctor", "smoke", "bench", "rehearse"}):
        v = report_get(rep)
        if v not in (None, ""):
            return str(v), f"fallback, no recorded runs: {src}"
    raise Missing(f"no recorded run header or Mac report gives {what}")


def macos_version(ctx):
    return from_headers_or_reports(ctx, lambda h: (h.get("machine") or {}).get("os"),
                                   lambda r: (r.get("machine") or {}).get("os"), "machine.os")


def llama_build(ctx):
    return from_headers_or_reports(ctx, lambda h: (h.get("model") or {}).get("server_build"),
                                   lambda r: r.get("llama_build"), "model.server_build / llama_build")


def swap_gb(ctx):
    vals = [(r.get("memory") or {}).get("swap_max_growth_mb") for r, _ in latest(ctx.reports, {"record"})]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        raise Missing("no record report (doctor/*-record-*.json) yet")
    return f1(max(vals) / 1024), f"max memory.swap_max_growth_mb / 1024 over {len(vals)} record reports"


def mem_gb(ctx):
    rows = [(r, s) for r, s in latest(ctx.reports, {"record"}) if (r.get("memory") or {}).get("used_gb") is not None]
    if rows:
        r, s = rows[0]
        return f1(r["memory"]["used_gb"]), f"{s} memory.used_gb"
    for rep, src in latest(ctx.reports, {"doctor"}):
        spec, mem, ram = rep.get("profile_spec") or {}, rep.get("memory") or {}, (rep.get("machine") or {}).get("ram_gb")
        prof = ctx.profile or {}
        if (rep.get("profile") == "default" and spec.get("quant") == prof.get("quant")
                and mem.get("free_pct_after") is not None and ram):
            used = ram * (100 - float(mem["free_pct_after"])) / 100
            return f1(used), (f"{src}: machine.ram_gb × (1 − memory.free_pct_after / 100) after the doctor run on the "
                              f"default profile (system-wide, memory_pressure); no record report carries memory use")
    raise Missing("no doctor or record report for the default profile with memory figures")


def schema_failures(ctx):
    if ctx.results is None:
        raise Missing("results/results.json not found")
    n = 0
    for r in ctx.runs:
        for e in ctx.events(r.run_id):
            if e.get("kind") != "error":
                continue
            pay = e.get("payload") or {}
            where = pay.get("where")
            if where in SCHEMA_WHERE or (where == "probe.scores" and str(pay.get("message", "")).startswith("invalid")):
                n += 1
    if not ctx.runs:
        raise Missing("no analysed LLM runs")
    return str(n), (f"error events where {sorted(SCHEMA_WHERE)} or invalid probe.scores, over the {len(ctx.runs)} "
                    f"analysed LLM runs")


def a5_runs(ctx):
    runs = [r for r in ctx.runs if r.arm == "A5"]
    want = ctx.arm("A5")["n_runs"]
    if len(runs) != want:
        raise Missing(f"runs/ has {len(runs)} analysed A5 runs but results.json says {want}; rerun "
                      f"python -m bench.results")
    return runs


def n_changed(ctx):
    runs = a5_runs(ctx)
    courses = [course_after_calibration(ctx.events(r.run_id)) for r in runs]
    n = sum(1 for c in courses if c["changed"])
    unknown = sum(1 for c in courses if c["changed"] is None)
    return str(n), (f"A5 runs whose first ranking call (query_pool sort_by / fit_surrogate) after the first "
                    f"calibrate_oracle differs from the last one before it; {unknown} of {len(runs)} runs had no "
                    f"call on one side and count as unchanged")


def showcase_course(ctx):
    runs = a5_runs(ctx)
    r = pick_showcase(runs)
    if r is None:
        raise Missing("no analysed A5 run on P1 (showcase rule, PREREG 9)")
    c = course_after_calibration(ctx.events(r.run_id))
    note = ""
    index = ctx.root / "runs" / "index.json"
    if index.exists():
        marked = [e.get("run_id") for e in json.loads(index.read_text()) if e.get("showcase")]
        if marked and marked != [r.run_id]:
            note = f"; runs/index.json marks {marked} as showcase, the rule picks {r.run_id}"
    return r, c, note


def what_it_did(ctx):
    r, c, note = showcase_course(ctx)
    src = f"showcase run {r.run_id} (PREREG 9 rule), trace around its first calibrate_oracle{note}"
    if not c["calibrated"]:
        raise Missing(f"needs a human: showcase run {r.run_id} has no calibrate_oracle result")
    if c["changed"] is None:
        raise Missing(f"needs a human: showcase run {r.run_id} has no ranking call on one side of its first "
                      f"calibrate_oracle; read the trace")
    rho = c.get("spearman")
    check = (f"checked the cheap simulator against its first {c['n']} DFT labels"
             + (f" (Spearman {float(rho):+.2f})" if rho is not None else ""))
    if c["changed"]:
        return f"{check}, then switched from ranking by {describe(c['before'])} to {describe(c['after'])}", src
    return f"{check} and kept ranking by {describe(c['before'])}", src


def what_it_used(ctx):
    r, c, note = showcase_course(ctx)
    src = f"showcase run {r.run_id}: first ranking call after its first calibrate_oracle{note}"
    before, after = c.get("before"), c.get("after")
    if c.get("changed") and before and before[1] in CHEAP_SORTS and after[1] not in CHEAP_SORTS:
        return describe(after), src
    if c.get("changed") is False:
        raise Missing(f"not needed: showcase run {r.run_id} kept {describe(before)} after the check, so VIDEO.md "
                      f"uses narration variant B")
    raise Missing(f"needs a human: showcase run {r.run_id} did not move from the cheap scores to another ranking "
                  f"(before {before}, after {after}); read the trace")


def rules(ctx):
    def res():
        return need(ctx.results, "results/results.json")

    return {
        **arm_values(ctx), **test_values(ctx),
        "RESULTS_STATUS": (lambda: res()["status"], "results.json status"),
        "PREREG_SHA": (lambda: prereg_sha256(ctx),
                       (f"sha256 of PREREG.md at tag {PREREG_TAG} (git show), the value run headers record as "
                        f"prereg_sha; results.json prereg_sha must be that tag's commit")),
        "HEADLINE": (lambda: headline(ctx), "PREREG 9 rule on results.json arms A5/A0/A2/A3 and tests A5 - A0/A2/A3"),
        "HEADLINE_SHORT": (lambda: headline(ctx, short=True), "results.json arms A5/A0/A2 and tests A5 - A0/A2"),
        "WIN_TIE_LOSS_SENTENCE": (lambda: win_tie_loss(ctx), "results.json tests A5 - A3 and A5 - A2, arms A2"),
        "H": (lambda: f2(ctx.arm("A5")["hits_mean"]), "results.json arms[A5 agent].hits_mean"),
        "RANDOM_HITS": (lambda: ceiling_mean(ctx, "random_expected"),
                        "mean of results.json ceilings[].random_expected (40 × hits ÷ 500)"),
        "ORACLE_HITS": (lambda: ceiling_mean(ctx, "oracle_hits"), "mean of results.json ceilings[].oracle_hits"),
        "BRIER": (lambda: f"{need(res()['calibration'], 'calibration')['brier']:.3f}", "results.json calibration.brier"),
        "BRIER_N": (lambda: str(need(res()["calibration"], "calibration")["n"]), "results.json calibration.n"),
        "VISUAL_RHO_MAE": (lambda: f2(need(need(res()["critic"], "critic")["visual_rho_mae"], "critic.visual_rho_mae")),
                           "results.json critic.visual_rho_mae (vision critiques only)"),
        "CLAIMS_N": (lambda: str(need(res()["claims"], "claims")["n"]), "results.json claims.n"),
        "CLAIMS_VERIFIED": (lambda: str(need(res()["claims"], "claims")["verified"]), "results.json claims.verified"),
        "CLAIMS_FLAGGED": (lambda: str(need(res()["claims"], "claims")["flagged"]), "results.json claims.flagged"),
        "LEAK_AUC": (lambda: f2(need(need(res()["leak"], "leak")["L_auc"], "leak.L_auc")), "results.json leak.L_auc"),
        "LEAK_SENTENCE": (lambda: leak_sentence(ctx),
                          "results.json leak.L_auc vs the 95% chance band of an AUC for the probe pool's hits"),
        "MACE_AUC": (lambda: f2(need(res()["honesty"]["mace_auc"], "mace_auc")), "results.json honesty.mace_auc"),
        "MACE_AUC_LO": (lambda: f2(need(res()["honesty"]["mace_auc_ci"], "mace_auc_ci")[0]),
                        "results.json honesty.mace_auc_ci[0]"),
        "MACE_AUC_HI": (lambda: f2(need(res()["honesty"]["mace_auc_ci"], "mace_auc_ci")[1]),
                        "results.json honesty.mace_auc_ci[1]"),
        "DFT_INIT_AUC": (lambda: f2(need(res()["honesty"]["dft_init_auc"], "dft_init_auc")),
                         "results.json honesty.dft_init_auc"),
        "SCHEMA_FAILURES": schema_failures,
        "N_CHANGED": n_changed,
        "WHAT_IT_DID": what_it_did,
        "WHAT_IT_USED": what_it_used,
        "TOKS": toks, "PREFILL_TOKS": prefill_toks, "CAMPAIGN_MIN": campaign_min,
        "DFLASH_RESULT": dflash_result, "DFLASH_SENTENCE": dflash_sentence,
        "MACOS_VERSION": macos_version, "LLAMA_BUILD": llama_build,
        "MEM_GB": mem_gb, "SWAP_GB": swap_gb,
        "FINAL_SHA": (lambda: need(git(ctx.root, "rev-parse", "--short=7", "HEAD"), "git HEAD"),
                      "git rev-parse --short=7 HEAD (the last commit before the docs are filled)"),
        "N_COMMITS": (lambda: n_commits(ctx), f"git rev-list --count {START_TAG}..HEAD"),
        "RUNS_RANGE": (lambda: runs_range(ctx), "first..last commit touching runs/ (git log -- runs/)"),
        "REPO_URL": (lambda: site_config(ctx, "REPO_URL"), "web/site.config.json REPO_URL"),
        "SITE_URL": (lambda: site_config(ctx, "SITE_URL"), "web/site.config.json SITE_URL"),
    }


def compute_values(ctx):
    """{name: Value} for every known placeholder (human-only ones have value None)."""
    out = {name: Value(None, f"needs a human: {why}", human=True) for name, why in HUMAN.items()}
    for name, rule in rules(ctx).items():
        try:
            if isinstance(rule, tuple):
                fn, src = rule
                out[name] = Value(str(fn()), src)
            else:
                v, src = rule(ctx)
                out[name] = Value(str(v), src)
        except Missing as e:
            out[name] = Value(None, str(e))
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as e:
            out[name] = Value(None, f"could not compute: {type(e).__name__}: {e}")
    return out


# ---- docs ------------------------------------------------------------------------------------------

def placeholder_name(inner):
    return inner.split(":", 1)[0].strip()


def scan_docs(root):
    """{file: [(full token, name)]} in document order, skipping the instructional {{...}}."""
    out = {}
    for f in DOCS:
        p = Path(root) / f
        if not p.exists():
            continue
        out[f] = [(m.group(0), placeholder_name(m.group(1))) for m in PLACEHOLDER.finditer(p.read_text())
                  if placeholder_name(m.group(1)) not in INSTRUCTIONAL]
    return out


def substitute(text, values):
    def rep(m):
        v = values.get(placeholder_name(m.group(1)))
        return v.value if v is not None and v.value is not None else m.group(0)
    return PLACEHOLDER.sub(rep, text)


def table(found, values):
    names = list(dict.fromkeys(n for toks in found.values() for _, n in toks))
    where = {n: [f for f, toks in found.items() if any(t[1] == n for t in toks)] for n in names}
    rows, missing, human, unknown = [], [], [], []
    for n in names:
        v = values.get(n)
        if v is None:
            unknown.append(n)
            rows.append((n, "UNKNOWN", f"no rule for this placeholder ({', '.join(where[n])})"))
        elif v.human:
            human.append(n)
            rows.append((n, "MISSING", v.source))
        elif v.value is None:
            missing.append(n)
            rows.append((n, "MISSING", v.source))
        else:
            rows.append((n, v.value, v.source))
    return rows, missing, human, unknown, where


def doc_values(ctx, values, where):
    return {
        "results_generated": (ctx.results or {}).get("generated"), "results_status": ctx.status,
        "head": git(ctx.root, "rev-parse", "HEAD"), "ref": ctx.ref,
        "values": {n: {"value": v.value, "source": v.source, "human": v.human, "files": where.get(n, [])}
                   for n, v in sorted(values.items())},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="print placeholder | value | source (default)")
    ap.add_argument("--write", action="store_true", help="substitute filled values in the docs, in place")
    ap.add_argument("--allow-mock", action="store_true", help="let --write use a results.json with status mock")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--ref", default=DEFAULT_REF, help="git ref holding the Mac reports doctor/*.json")
    a = ap.parse_args([] if argv is None else argv)
    ctx = Context(a.root, a.ref)
    values = compute_values(ctx)
    found = scan_docs(ctx.root)
    rows, missing, human, unknown, where = table(found, values)

    if a.check or not a.write:
        print(f"results.json status: {ctx.status or 'absent'}; {len(ctx.runs)} analysed LLM runs, "
              f"{len(ctx.recorded)} recorded runs, {len(ctx.reports)} Mac reports (local doctor/ + {ctx.ref})")
        if ctx.schema_errors:
            print(f"warning: results.json does not match the schema: {ctx.schema_errors[:3]}")
        print("| placeholder | value | source |\n|---|---|---|")
        for n, v, s in rows:
            print(f"| {n} | {v} | {s} |")
        print(f"\n{len(rows)} placeholders: {len(rows) - len(missing) - len(human) - len(unknown)} filled, "
              f"{len(missing)} MISSING, {len(human)} need a human, {len(unknown)} unknown")
        if human:
            print("needs a human: " + ", ".join(human))
        if unknown:
            print("unknown placeholder names: " + ", ".join(unknown))
        if not a.write:
            return 1 if missing or unknown else 0

    refusal = None
    if ctx.results is None:
        refusal = "results/results.json not found; run python -m bench.results first"
    elif ctx.schema_errors:
        refusal = f"results.json does not match the schema ({ctx.schema_errors[0]})"
    elif ctx.status == "mock" and not a.allow_mock:
        refusal = "results.json has status mock; refusing to put mock numbers in the docs (pass --allow-mock)"
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 2
    if ctx.status != "final":
        print(f"warning: results.json status is {ctx.status!r}, not 'final'")
    for f in found:
        p = ctx.root / f
        old = p.read_text()
        new = substitute(old, values)
        if new != old:
            p.write_text(new)
        n = sum(1 for _, name in found[f] if values.get(name) and values[name].value is not None)
        print(f"{f}: {n} of {len(found[f])} placeholders filled")
    out = ctx.root / "results" / "doc_values.json"
    out.write_text(json.dumps(doc_values(ctx, values, where), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out.relative_to(ctx.root)}; still unfilled: "
          + (", ".join(missing + human + unknown) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
