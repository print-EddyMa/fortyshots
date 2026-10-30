"""Generate a realistic, contract-shaped fixture dataset for the web app.

Writes web/fixtures/{runs/index.json, runs/<run_id>/{events.jsonl,summary.json,img/*.png},
results/results.json, results/pools.json}.

Everything here is MOCK data, made only so the site can be built and tested before
real runs exist. IDs, structures, DFT labels and initial-geometry DFT energies are real
(from data/); MACE values and pore descriptors are SYNTHETIC because the MACE cache
and descriptors.parquet are not built yet. results.json carries status "mock".

Run: ../.venv/bin/python scripts/make_fixtures.py   (from web/)
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = ROOT / "web" / "fixtures"
SEED = 20261029

SYMBOLS = (
    "X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu "
    "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu"
).split()
NONMETAL = {1, 2, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17, 18, 33, 34, 35, 36, 52, 53, 54}
CHEAP = {"Li", "Na", "K", "Mg", "Ca", "Al", "Fe", "Zn", "Mn", "Ti"}
MID = {"Cu", "Ni", "Co", "Cr", "Zr", "Sr", "Ba", "V", "Ga", "Sn", "Bi", "Pb", "Cd", "Y", "Mo", "W"}
CPK = {
    "H": "#ffffff", "C": "#909090", "N": "#3050f8", "O": "#ff0d0d", "F": "#90e050", "S": "#ffff30",
    "Cl": "#1ff01f", "P": "#ff8000", "Zn": "#7d80b0", "Cu": "#c88033", "Mg": "#8aff00", "Co": "#f090a0",
    "Ni": "#50d050", "Fe": "#e06633", "Mn": "#9c7ac7", "Ca": "#3dff00", "Al": "#bfa6a6", "Cd": "#ffd98f",
}

MODEL = {
    "name": "muse-glimmer-30b",
    "gguf": "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
    "sha256": "MOCK-0000000000000000000000000000000000000000000000000000000000000000"[:64],
    "quant": "Q4_K_M",
    "server_build": "mock-llm (fixture)",
    "flags": ["--jinja", "-c 32768", "-ngl 99", "--mmproj mmproj-Muse-Glimmer-30B-Q4_K_M.gguf",
              "--reasoning-budget 1024", "--parallel 1", "--cache-reuse 256"],
}
MACHINE = {"chip": "Apple M4 (fixture)", "ram_gb": 24, "os": "macOS 15.6"}


# --------------------------------------------------------------------------- data

def hill_formula(numbers: list[int]) -> str:
    counts: dict[str, int] = {}
    for z in numbers:
        s = SYMBOLS[z]
        counts[s] = counts.get(s, 0) + 1
    g = 0
    for v in counts.values():
        g = math.gcd(g, v)
    keys = sorted(counts, key=lambda s: (s != "C", s != "H", s))
    if "C" not in counts:
        keys = sorted(counts)
    return "".join(f"{k}{counts[k] // g if counts[k] // g > 1 else ''}" for k in keys)


def load_table() -> pd.DataFrame:
    uni = pd.read_parquet(DATA / "universe.parquet")
    lab = pd.read_parquet(DATA / "labels.parquet")
    df = uni.merge(lab, on="id")
    rng = np.random.default_rng(SEED)
    formulas, metals, tiers, has = [], [], [], []
    for mid in df["id"]:
        fp = DATA / "structures" / f"{mid}.json"
        has.append(fp.exists())
        if not fp.exists():
            formulas.append(None)
            metals.append("")
            tiers.append(2)
            continue
        s = json.loads(fp.read_text())
        ads = set(s["ads"])
        fw = [z for i, z in enumerate(s["numbers"]) if i not in ads]
        formulas.append(hill_formula(fw))
        ms = sorted({SYMBOLS[z] for z in fw if z not in NONMETAL})
        metals.append(",".join(ms))
        tier = 1
        for m in ms:
            tier = max(tier, 1 if m in CHEAP else 2 if m in MID else 3)
        tiers.append(tier)
    df["has_struct"] = has
    df["formula"] = formulas
    df["metals"] = metals
    df["cost_tier"] = tiers
    # SYNTHETIC cheap-oracle values: weakly tied to the initial-geometry DFT, plus heavy noise,
    # so that, as measured in research/, MACE has essentially no hit-ranking signal.
    n = len(df)
    df["E_CO2_mace"] = (0.06 * df["E_CO2_init"].clip(-2, 1) - 0.12 + rng.normal(0, 0.32, n)).round(4)
    df["E_H2O_mace"] = (0.06 * df["E_H2O_init"].clip(-2, 1) - 0.20 + rng.normal(0, 0.32, n)).round(4)
    df["S_mace"] = (df["E_CO2_mace"] + np.maximum(0, df["E_CO2_mace"] - df["E_H2O_mace"] + 0.1)).round(4)
    df["S"] = df["E_CO2"] + np.maximum(0, df["E_CO2"] - df["E_H2O"] + 0.1)
    # SYNTHETIC pore descriptors; hits lean slightly toward narrow pores so A1 is a fair heuristic.
    lcd = np.exp(rng.normal(math.log(6.2), 0.35, n)) - df["hit"].astype(float) * rng.uniform(0, 1.6, n)
    df["lcd_A"] = np.clip(lcd, 3.0, 18).round(2)
    df["pld_A"] = (df["lcd_A"] * rng.uniform(0.55, 0.92, n)).clip(2.4, None).round(2)
    oms_p = np.where(df["hit"], 0.62, 0.33)
    df["oms"] = rng.uniform(0, 1, n) < oms_p
    df["void_fraction"] = rng.uniform(0.25, 0.75, n).round(3)
    df["density_gcm3"] = rng.uniform(0.7, 2.1, n).round(3)
    df["asa_m2g"] = rng.uniform(150, 2600, n).round(0)
    return df


# --------------------------------------------------------------------------- images

def save_parity(path: Path, bought: pd.DataFrame, rho: float | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.12, 5.12), dpi=100)
    fig.patch.set_facecolor("#ffffff")
    x, y = bought["S_mace"], bought["S"]
    lo, hi = min(x.min(), y.min()) - 0.1, max(x.max(), y.max()) + 0.1
    ax.plot([lo, hi], [lo, hi], ls="--", lw=1, color="#bbbbbb")
    hit = bought["hit"].astype(bool)
    ax.scatter(x[~hit], y[~hit], s=26, facecolors="none", edgecolors="#333333", lw=1, label="miss")
    ax.scatter(x[hit], y[hit], s=26, color="#c2410c", label="hit")
    ax.set_xlabel("MACE S (eV)")
    ax.set_ylabel("DFT S (eV)")
    ax.set_title(f"cheap oracle vs DFT, n = {len(bought)}" + (f", Spearman {rho:.2f}" if rho is not None else ""),
                 fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def save_render(path: Path, mid: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = json.loads((DATA / "structures" / f"{mid}.json").read_text())
    pos = np.array(s["pos"])
    nums = s["numbers"]
    ads = set(s["ads"])
    fig = plt.figure(figsize=(5.12, 5.12), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    fig.patch.set_facecolor("#141414")
    ax.set_facecolor("#141414")
    order = np.argsort(pos[:, 2])
    for i in order:
        sym = SYMBOLS[nums[i]]
        c = CPK.get(sym, "#c0a0c0")
        size = 30 if sym == "H" else 70 if nums[i] in NONMETAL else 140
        ax.scatter(pos[i, 0], pos[i, 1], s=size, color=c, edgecolors="#000000", linewidths=0.4, zorder=2)
    ap = pos[list(ads)]
    ax.scatter(ap[:, 0], ap[:, 1], s=900, color="#f59e4b", alpha=0.18, zorder=1)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


# --------------------------------------------------------------------------- run generator

def spearman(a, b) -> float | None:
    if len(a) < 3:
        return None
    from scipy.stats import spearmanr
    r = spearmanr(a, b).correlation
    return None if r is None or np.isnan(r) else round(float(r), 3)


def auc(scores, labels) -> float | None:
    labels = np.asarray(labels, bool)
    if labels.all() or (~labels).all():
        return None
    from sklearn.metrics import roc_auc_score
    return round(float(roc_auc_score(labels, -np.asarray(scores))), 3)


DESC_COLS = ["formula", "metals", "oms", "pld_A", "lcd_A", "void_fraction", "density_gcm3", "asa_m2g", "cost_tier"]


def table(rows: list[dict], **extra) -> dict:
    """Table-shaped tool result, as the agent core writes it: {columns, rows: [[...]]} (CONTRACTS addenda)."""
    cols = list(rows[0].keys()) if rows else ["id"]
    return {**extra, "columns": cols, "rows": [[r.get(c) for c in cols] for r in rows]}


def row(df: pd.DataFrame, mid: str, cols=None) -> dict:
    r = df.loc[mid]
    cols = cols or (DESC_COLS + ["E_CO2_mace", "E_H2O_mace", "S_mace"])
    out = {"id": mid}
    for c in cols:
        v = r[c]
        if isinstance(v, (np.bool_, bool)):
            v = bool(v)
        elif isinstance(v, (np.integer,)):
            v = int(v)
        elif isinstance(v, (np.floating, float)):
            v = round(float(v), 4)
        out[c] = v
    return out


class Run:
    def __init__(self, df: pd.DataFrame, arm: str, pool: str, seed: int, start: datetime, rng):
        self.df = df
        self.pool_df = df[df["pool"] == pool].set_index("id", drop=False)
        self.arm, self.pool, self.seed = arm, pool, seed
        self.rng = rng
        self.run_id = f"mock-{arm}-{pool}-s{seed}-{start.strftime('%Y%m%d%H%M')}"
        self.t = start
        self.start = start
        self.seq = 0
        self.call = 0
        self.events: list[dict] = []
        self.bought: list[str] = []
        self.p_hits: list[float | None] = []
        self.dir = OUT / "runs" / self.run_id
        if self.dir.exists():
            shutil.rmtree(self.dir)
        (self.dir / "img").mkdir(parents=True)
        self.cache_n = 0
        self.n_llm = 0
        self.schema_fail = 0
        self.vetoed: set[str] = set()

    # -- helpers
    def tick(self, seconds: float):
        self.t += timedelta(seconds=float(seconds))

    def emit(self, rnd, role, kind, payload, call_id=None, tokens=None, timings=None, dur_ms=None):
        self.seq += 1
        ev = {"seq": self.seq, "t": self.t.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "run_id": self.run_id, "round": rnd, "role": role, "kind": kind, "payload": payload,
              "call_id": call_id, "tokens": tokens, "timings": timings, "dur_ms": dur_ms}
        self.events.append(ev)
        return ev

    def cid(self) -> str:
        self.call += 1
        return f"c{self.call:04d}"

    def llm(self, rnd, role, reasoning, *, strength="medium", n_messages=4, n_images=0, schema=None,
            finish="tool_calls", message=None, completion=None):
        self.n_llm += 1
        prompt_n = int(self.rng.integers(2200, 9000))
        cache_n = int(prompt_n * self.rng.uniform(0.55, 0.92)) if self.n_llm > 1 else 0
        rtoks = max(60, int(len(reasoning.split()) * 1.35))
        comp = completion or int(self.rng.integers(80, 420))
        pps = float(self.rng.uniform(170, 260))
        tps = float(self.rng.uniform(10.8, 12.9)) - 0.6 * (rnd / 4)
        dur = ((prompt_n - cache_n) / pps + (rtoks + comp) / tps) * 1000
        c = self.cid()
        tokens = {"prompt": prompt_n, "completion": comp, "reasoning": rtoks}
        timings = {"prompt_n": prompt_n - cache_n, "prompt_per_second": round(pps, 2),
                   "predicted_n": rtoks + comp, "predicted_per_second": round(tps, 2), "cache_n": cache_n}
        self.emit(rnd, role, "llm_call", {"model": MODEL["name"], "reasoning_strength": strength,
                                          "n_messages": n_messages, "n_images": n_images,
                                          "finish_reason": finish, "schema": schema,
                                          "n_tool_calls": 0 if finish != "tool_calls" else 2},
                  call_id=c, tokens=tokens, timings=timings, dur_ms=int(dur))
        self.tick(dur / 1000 * 0.85)
        self.emit(rnd, role, "reasoning", {"text": reasoning}, call_id=c)
        self.tick(dur / 1000 * 0.15)
        if message:
            self.emit(rnd, role, "message", {"text": message}, call_id=c)
        return c

    def tool(self, rnd, role, name, args, result, summary, image=None, ok=True, secs=None):
        c = self.cid()
        self.emit(rnd, role, "tool_call", {"name": name, "args": args}, call_id=c)
        d = secs if secs is not None else float(self.rng.uniform(0.05, 0.6))
        self.tick(d)
        self.emit(rnd, role, "tool_result", {"name": name, "ok": ok, "summary": summary[:120], "result": result,
                                             "image": image}, call_id=c, dur_ms=int(d * 1000))
        return c

    def tools_parallel(self, rnd, role, specs):
        """specs: list of (name, args, result, summary, image). All calls are emitted first, then results."""
        cids = []
        for name, args, *_ in specs:
            c = self.cid()
            cids.append(c)
            self.emit(rnd, role, "tool_call", {"name": name, "args": args}, call_id=c)
        for c, (name, args, result, summary, image) in zip(cids, specs):
            d = float(self.rng.uniform(0.05, 0.5))
            self.tick(d)
            self.emit(rnd, role, "tool_result", {"name": name, "ok": True, "summary": summary[:120],
                                                 "result": result, "image": image}, call_id=c, dur_ms=int(d * 1000))
        return cids

    def unbought(self):
        return self.pool_df[~self.pool_df["id"].isin(self.bought)]

    # -- tool emulations
    def q_pool(self, filters: dict, sort_by: str, ascending=True, limit=25):
        d = self.unbought()
        d = d[d["has_struct"]]
        if "metal_in" in filters:
            d = d[d["metals"].apply(lambda m: any(x in m.split(",") for x in filters["metal_in"]))]
        if "oms" in filters:
            d = d[d["oms"] == filters["oms"]]
        if "pld_min" in filters:
            d = d[d["pld_A"] >= filters["pld_min"]]
        if "pld_max" in filters:
            d = d[d["pld_A"] <= filters["pld_max"]]
        if "cost_tier_max" in filters:
            d = d[d["cost_tier"] <= filters["cost_tier_max"]]
        d = d.sort_values(sort_by, ascending=ascending).head(limit)
        rows = [row(self.df.set_index("id", drop=False), m) for m in d["id"]]
        return table(rows, n_match=int(len(d))), d

    def buy(self, rnd, ids, rationale, p_hits):
        spent_before = len(self.bought)
        self.emit(rnd, "system", "dft_order", {"ids": ids, "rationale": rationale,
                                                "spent": spent_before + len(ids),
                                                "remaining": 40 - spent_before - len(ids)})
        res = [{"id": m, "E_CO2": round(float(self.pool_df.loc[m, "E_CO2"]), 4),
                "E_H2O": round(float(self.pool_df.loc[m, "E_H2O"]), 4),
                "hit": bool(self.pool_df.loc[m, "hit"])} for m in ids]
        nh = sum(r["hit"] for r in res)
        full = {"results": [{**r, "S": round(float(self.pool_df.loc[r["id"], "S"]), 4)} for r in res], "hits_new": nh,
                "hits_total": int(self.pool_df.loc[self.bought + ids, "hit"].sum()), "spent": spent_before + len(ids),
                "remaining": 40 - spent_before - len(ids), "already_bought_not_charged": []}
        c = self.tool(rnd, "system", "request_dft", {"ids": ids, "rationale": rationale}, full,
                      f"{len(ids)} DFT lookups, {nh} hit{'s' if nh != 1 else ''}, {spent_before + len(ids)}/40 spent",
                      secs=0.2)
        self.bought.extend(ids)
        self.p_hits.extend(p_hits)
        self.tick(0.05)
        hits_total = int(self.pool_df.loc[self.bought, "hit"].sum())
        self.emit(rnd, "system", "dft_result", {"results": res, "hits_total": hits_total, "spent": len(self.bought)},
                  call_id=c)
        return c, nh

    def calibrate(self, rnd):
        b = self.pool_df.loc[self.bought]
        rho = spearman(b["S_mace"], b["S"])
        a = auc(b["S_mace"], b["hit"])
        img = f"img/calibration-r{rnd}.png"
        save_parity(self.dir / img, b, rho)
        res = {"n": len(b), "spearman": rho, "auc": a}
        c = self.tool(rnd, "director", "calibrate_oracle", {}, res,
                      f"n={len(b)}, Spearman {rho:+.2f}, hit AUC {a if a is not None else '—'}", image=img, secs=1.4)
        self.emit(rnd, "system", "calibration", {"n": len(b), "spearman": rho, "auc": a, "image": img}, call_id=c)
        return c, rho, a

    # -- selection with a target number of hits (fixture only: we know the labels)
    def choose(self, cand_ids: list[str], k: int, want_hits: int) -> list[str]:
        cand = [m for m in cand_ids if m not in self.bought and m not in self.vetoed]
        hits = [m for m in cand if self.pool_df.loc[m, "hit"]]
        miss = [m for m in cand if not self.pool_df.loc[m, "hit"]]
        if len(hits) < want_hits:
            extra = [m for m in self.unbought()["id"] if self.pool_df.loc[m, "hit"] and m not in hits
                     and self.pool_df.loc[m, "has_struct"]]
            hits += list(self.rng.permutation(extra))[: want_hits - len(hits)]
        pick = hits[:want_hits] + miss[: k - min(want_hits, len(hits))]
        if len(pick) < k:
            rest = [m for m in self.unbought()["id"] if m not in pick and not self.pool_df.loc[m, "hit"]
                    and self.pool_df.loc[m, "has_struct"]]
            rest = [m for m in rest if m not in self.vetoed]
            pick += list(self.rng.permutation(rest))[: k - len(pick)]
        order = self.rng.permutation(len(pick))
        return [pick[i] for i in order][:k]

    def p_hit_for(self, mid, base):
        h = bool(self.pool_df.loc[mid, "hit"])
        p = base + (0.12 if h else 0) + float(self.rng.normal(0, 0.06))
        return round(float(np.clip(p, 0.02, 0.85)), 2)

    def short(self, mid):
        r = self.pool_df.loc[mid]
        return f"{mid} ({r['metals'] or 'no metal'}, PLD {r['pld_A']:.1f} Å, S_mace {r['S_mace']:+.2f})"

    def write(self, extra_summary: dict):
        flags = [bool(self.pool_df.loc[m, "hit"]) for m in self.bought]
        first = next((i + 1 for i, f in enumerate(flags) if f), None)
        ps = [p for p in self.p_hits]
        brier = None
        if all(p is not None for p in ps) and ps:
            brier = round(float(np.mean([(p - f) ** 2 for p, f in zip(ps, flags)])), 4)
        summary = {"run_id": self.run_id, "arm": self.arm, "pool": self.pool, "seed": self.seed, "mode": "mock",
                   "model": MODEL["name"], "hits": int(sum(flags)),
                   "spent": len(self.bought), "picks": list(self.bought), "hit_flags": flags,
                   "lookups_to_first_hit": first, "brier": brier, "p_hits": ps,
                   "duration_s": int((self.t - self.start).total_seconds()) + 2, "aborted": False, "autofill": 0}
        summary.update(extra_summary)
        self.tick(1.0)
        self.emit(5, "system", "summary", summary)
        with open(self.dir / "events.jsonl", "w") as f:
            for e in self.events:
                f.write(json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n")
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1))
        return summary


def header(run: Run, rounds=4, batch=10):
    run.emit(0, "system", "header", {
        "arm": run.arm, "pool": run.pool, "seed": run.seed, "budget": 40, "rounds": rounds, "batch": batch,
        "goal": "Find MOFs that bind CO2 more strongly than water, from humid air, using 40 DFT lookups.",
        "mode": "mock", "model": MODEL, "machine": MACHINE, "network": "off",
        "prereg_sha": "mock000", "git_sha": "mock000",
        "config": {"fixture": True, "reasoning_strength": "medium", "action_mode": "native"},
    })
    run.tick(0.8)


def agent_run(df, arm, pool, seed, start, hit_plan, rng, *, surrogate=True):
    """A5 / A6 campaign: 4 rounds of 10 with Director, Proposer, Critic, Reporter."""
    run = Run(df, arm, pool, seed, start, rng)
    header(run)
    idx_df = df.set_index("id", drop=False)
    evidence_rows: dict[str, str] = {}
    critic_rhos: list[tuple[float | None, float | None]] = []
    report_cites: dict[str, str] = {}
    best: tuple[float, str] | None = None

    for rnd in range(1, 5):
        # ---------------- Director
        if rnd == 1:
            reasoning = (
                "Goal: CO2 that wins against water, so I need E_CO2 below -0.5 eV and below E_H2O. The pool has 500 "
                "frameworks and I can afford 40 lookups, 10 per round. I have no labels yet, so the only signal is the "
                "cheap oracle and the pore descriptors. Plan for round 1: look at the MACE ranking, look at narrow-pore "
                "frameworks with open metal sites (those polarize CO2), and keep the cost tier low because the goal says "
                "cheap metals. I will run both queries in parallel and render the top candidate to check the binding "
                "site is in a pore and not on the outer surface."
            )
            run.llm(rnd, "director", reasoning, n_messages=3)
            r1, d1 = run.q_pool({"cost_tier_max": 2}, "S_mace", True, 25)
            r2, d2 = run.q_pool({"oms": True, "pld_min": 3.3, "pld_max": 6.0}, "pld_A", True, 25)
            cids = run.tools_parallel(rnd, "director", [
                ("query_pool", {"filters": {"cost_tier_max": 2, "exclude_bought": True}, "sort_by": "S_mace",
                                "ascending": True, "limit": 25}, r1,
                 f"25 rows; S_mace {d1['S_mace'].min():+.2f} to {d1['S_mace'].max():+.2f} eV", None),
                ("query_pool", {"filters": {"oms": True, "pld_min": 3.3, "pld_max": 6.0, "exclude_bought": True},
                                "sort_by": "pld_A", "ascending": True, "limit": 25}, r2,
                 f"{len(r2['rows'])} rows with open metal sites, PLD {d2['pld_A'].min():.1f}–{d2['pld_A'].max():.1f} Å", None),
            ])
            top = d1["id"].iloc[0]
            img = f"img/render-{top}.png"
            save_render(run.dir / img, top)
            rc = run.tool(rnd, "director", "render_structure", {"id": top}, {"id": top, "natoms": int(
                idx_df.loc[top, "natoms_framework"]) + 3}, f"512 px render of {top}, CO2 at best MACE placement",
                image=img, secs=0.9)
            cand = list(d1["id"].iloc[:12]) + list(d2["id"].iloc[:8])
            mace_top = list(d1["id"])
            for m in d1["id"]:
                evidence_rows[m] = cids[0]
            for m in d2["id"]:
                evidence_rows.setdefault(m, cids[1])
            report_cites["mace"] = cids[0]
            report_cites["oms"] = cids[1]
            report_cites["render"] = rc
            prop_reason = (
                "Round 1 has no feedback, so this batch is a probe. Seven picks come from the MACE ranking (lowest "
                "S_mace among cheap metals), three from the narrow-pore open-metal-site list. P(hit) is low across the "
                "board: the base rate is about 2.8%, and I have no evidence yet that MACE ranks this pool well. I will "
                "keep every probability under 0.3."
            )
            base_p = 0.12
        else:
            cc, rho, a = run.calibrate(rnd)
            report_cites[f"cal{rnd}"] = cc
            if rnd == 2:
                reasoning = (
                    f"Calibration on the {len(run.bought)} labels I bought: Spearman between MACE S and DFT S is "
                    f"{rho:+.2f}" + (f" and hit AUC is {a:.2f}" if a is not None else " (no hits yet, so no AUC)") +
                    (". That is no ranking signal." if abs(rho) < 0.2 else ", on n = 10 that is too weak to trust.") +
                    " The round-1 picks were the best MACE scores and they did not bind better than average. "
                    "The parity plot is a cloud, not a line. I should stop trusting the MACE order. Shift: use pore "
                    "descriptors and open metal sites as the main signal, and "
                    + ("fit a surrogate on the labels once I have enough of them." if surrogate else
                       "rank by my own read of the descriptors, since I have no surrogate tool.")
                )
            else:
                reasoning = (
                    f"Calibration now on n = {len(run.bought)}: Spearman {rho:+.2f}. Still no usable signal from MACE. "
                    f"Hits so far: {int(run.pool_df.loc[run.bought, 'hit'].sum())}. The hits share narrow pores "
                    "(PLD under 5 Å) and open metal sites. Keep exploiting that region, and spend two picks on "
                    "frameworks unlike anything bought so far so the surrogate does not overfit."
                )
            run.llm(rnd, "director", reasoning, n_messages=5 + 2 * rnd)
            specs = [("query_pool", {"filters": {"oms": True, "pld_max": 5.0, "exclude_bought": True},
                                     "sort_by": "pld_A", "ascending": True, "limit": 25}, None, None, None)]
            r2, d2 = run.q_pool({"oms": True, "pld_max": 5.0}, "pld_A", True, 25)
            specs[0] = (specs[0][0], specs[0][1], r2, f"{len(r2['rows'])} rows, OMS and PLD ≤ 5.0 Å", None)
            if surrogate and len(run.bought) >= 5:
                d3 = run.unbought().copy()
                score = (d3["pld_A"] / 6 - d3["oms"].astype(float) * 0.5 + rng.normal(0, 0.3, len(d3)))
                d3 = d3.assign(mean=(-0.45 + 0.25 * score).round(3), std=rng.uniform(0.08, 0.25, len(d3)).round(3))
                d3["acq"] = d3["mean"] - d3["std"]
                d3 = d3.sort_values("acq").head(25)
                rows = [{**row(idx_df, m, ["formula", "metals", "pld_A", "oms", "S_mace"]),
                         "mean": float(d3.loc[m, "mean"]), "std": float(d3.loc[m, "std"])} for m in d3["id"]]
                sur_rows = rows
                specs.append(("fit_surrogate", {"kappa": 1.0, "top_k": 25}, table(rows, n_train=len(run.bought), kappa=1.0),
                              f"RF on {len(run.bought)} labels; top-25 mean S {d3['mean'].min():+.2f} to "
                              f"{d3['mean'].max():+.2f} eV", None))
            cids = run.tools_parallel(rnd, "director", specs)
            for m in d2["id"]:
                evidence_rows[m] = cids[0]
            cand = list(d2["id"].iloc[:14])
            if surrogate and len(cids) > 1:
                report_cites[f"sur{rnd}"] = cids[1]
                for r in sur_rows:
                    evidence_rows[r["id"]] = cids[1]
                cand = [r["id"] for r in sur_rows[:8]] + cand
            prop_reason = (
                "Batch from the surrogate's lowest mean − std and the narrow-pore OMS list. I raise P(hit) for frameworks "
                "that look like the round's hits (open metal site, PLD under 5 Å) and lower it for wide pores. I give no "
                "weight to S_mace, since calibration shows it does not rank this pool."
            )
            base_p = 0.18 + 0.03 * rnd

        # ---------------- Proposer
        picks = run.choose(cand, 10, hit_plan[rnd - 1])
        if rnd == 3:
            run.llm(rnd, "proposer", prop_reason, schema="Batch", finish="stop", n_messages=6)
            run.schema_fail += 1
            run.emit(rnd, "proposer", "error", {"where": "proposer.schema",
                                                 "message": "Batch.picks[7].p_hit = 1.3 is above 1.0; retrying (1/2)",
                                                 "recovered": True})
            run.tick(0.3)
            prop_reason2 = "Retry: P(hit) must be a probability. Clamp to my actual belief; the batch is unchanged."
            run.llm(rnd, "proposer", prop_reason2, schema="Batch", finish="stop", n_messages=8)
        else:
            run.llm(rnd, "proposer", prop_reason, schema="Batch", finish="stop", n_messages=6)
        pick_objs = []
        for m in picks:
            r = idx_df.loc[m]
            why = (f"OMS {'yes' if r['oms'] else 'no'}, PLD {r['pld_A']:.1f} Å, metals {r['metals'] or '—'}"
                   + (f", S_mace {r['S_mace']:+.2f} eV" if rnd == 1 else ""))
            pick_objs.append({"id": m, "p_hit": run.p_hit_for(m, base_p), "rationale": why,
                              "evidence": [evidence_rows.get(m, cids[0])]})
        run.emit(rnd, "proposer", "proposal", {"picks": pick_objs, "stage": "probe" if rnd == 1 else "initial",
                                                "replaced": []})
        run.tick(0.2)

        # ---------------- Critic (vision)
        cal_img = None if rnd == 1 else f"img/calibration-r{rnd}.png"
        n_img = 1 if rnd == 1 else 2
        veto = [p for p in pick_objs if not run.pool_df.loc[p["id"], "hit"]][-1]
        run.vetoed.add(veto["id"])
        vr = None
        if rnd > 1:
            cal = [e for e in run.events if e["kind"] == "calibration"][-1]["payload"]["spearman"] or 0.0
            vr = round(float(cal + rng.normal(0, 0.08)), 2)
            critic_rhos.append((vr, cal))
        crit_reason = (
            ("I can see the render: the CO2 sits " + ("inside a narrow channel next to the metal. " if rnd == 1 else ""))
            if rnd == 1 else
            f"Reading the parity plot: points spread across the box with no diagonal trend. I read the rank "
            f"correlation as about {vr:+.2f}. "
        ) + (f"One pick, {veto['id']}, has a pore wider than anything that has hit so far and no open metal site; "
             "water will compete for the same site. Veto it and ask for a replacement.")
        run.llm(rnd, "critic", crit_reason, schema="Review", finish="stop", n_images=n_img, n_messages=3)
        run.emit(rnd, "critic", "critique", {
            "visual_rho": vr,
            "vetoes": [{"id": veto["id"], "reason": "wide pore and no open metal site; water likely wins",
                        "evidence": veto["evidence"]}],
            "notes": ("Render shows the guest in a channel; MACE placement looks physical." if rnd == 1 else
                      f"Parity plot reads as {'no' if abs(vr) < 0.2 else 'weak'} signal (visual ρ ≈ {vr:+.2f}). "
                      "Do not rank by S_mace."),
            "vision": True,
        })
        run.tick(0.2)
        # replacement
        repl = run.choose([m for m in cand if m not in picks], 1, 0)[0]
        r = idx_df.loc[repl]
        new_pick = {"id": repl, "p_hit": run.p_hit_for(repl, base_p),
                    "rationale": f"replacement for vetoed {veto['id']}: OMS {'yes' if r['oms'] else 'no'}, "
                                 f"PLD {r['pld_A']:.1f} Å", "evidence": [evidence_rows.get(repl, cids[0])]}
        run.llm(rnd, "proposer", f"Replace {veto['id']} with {repl}; the rest of the batch stands.",
                schema="Batch", finish="stop", n_messages=8, completion=140)
        pick_objs = [p if p["id"] != veto["id"] else new_pick for p in pick_objs]
        run.emit(rnd, "proposer", "proposal", {"picks": pick_objs, "stage": "revised",
                                                "replaced": [{"id": veto["id"], "by": repl}]})
        run.tick(0.3)
        run.emit(rnd, "human", "human", {"action": "auto", "ids": [p["id"] for p in pick_objs],
                                         "note": "recorded run: batch auto-approved"})
        run.tick(0.2)
        ids = [p["id"] for p in pick_objs]
        c, nh = run.buy(rnd, ids, "round %d batch" % rnd + (" (probe)" if rnd == 1 else ""),
                        [p["p_hit"] for p in pick_objs])
        report_cites[f"dft{rnd}"] = c
        for m in ids:
            s = float(run.pool_df.loc[m, "S"])
            if best is None or s < best[0]:
                best = (s, m)
        run.tick(2.0)

    # ---------------- Reporter
    hits = [m for m in run.bought if run.pool_df.loc[m, "hit"]]
    hits_sorted = sorted(hits, key=lambda m: run.pool_df.loc[m, "S"])
    first_hit = next((i + 1 for i, m in enumerate(run.bought) if run.pool_df.loc[m, "hit"]), None)
    rho2 = [e for e in run.events if e["kind"] == "calibration"][0]["payload"]["spearman"]
    rep_reason = "Write the report. Cite the call for every number. Keep it short; list hits in order of DFT score."
    run.llm(5, "reporter", rep_reason, strength="low", schema=None, finish="stop", n_messages=12, completion=520)
    lines = [
        f"Campaign {run.run_id} spent {len(run.bought)} of 40 DFT lookups and found {len(hits)} hits "
        f"[{report_cites['dft4']}].",
        f"The first hit came at lookup {first_hit} [{report_cites['dft1'] if first_hit and first_hit <= 10 else report_cites['dft2']}].",
        f"Round 1 followed the MACE ranking [{report_cites['mace']}]. Calibration after round 1 gave Spearman "
        f"{rho2:+.2f} between MACE S and DFT S [{report_cites['cal2']}], so I stopped ranking by S_mace and moved to "
        "pore size and open metal sites" + (f" plus the surrogate [{report_cites['sur2']}]." if 'sur2' in report_cites else "."),
    ]
    if hits_sorted:
        h0 = hits_sorted[0]
        lines.append(f"The strongest hit is {h0}: E_CO2 {run.pool_df.loc[h0, 'E_CO2']:+.2f} eV against E_H2O "
                     f"{run.pool_df.loc[h0, 'E_H2O']:+.2f} eV [{report_cites['dft' + str(1 + run.bought.index(h0) // 10)]}].")
    lines.append(f"Hits cluster at PLD under 5 Å with open metal sites [{report_cites['oms']}].")
    lines.append("Every hit was bought at a stated P(hit); the calibration of those probabilities is scored blind.")
    # one deliberately wrong number so the claim checker has something to flag
    n_top = sum(1 for m in hits if m in mace_top)
    lines.append(f"MACE ranked {n_top + 2} of the hits in its top 25 [{report_cites['mace']}].")
    report = " ".join(lines)
    run.emit(5, "reporter", "report", {"text": report, "source": "model", "written_by": "Muse Glimmer 30B (fixture)"})
    run.tick(0.6)
    n_claims = 9 + len(hits_sorted[:1])
    run.emit(5, "system", "claimcheck", {"n_claims": n_claims, "verified": n_claims - 1, "flagged": [
        {"claim": f"MACE ranked {n_top + 2} of the hits in its top 25",
         "reason": f"{report_cites['mace']} lists 25 ids, of which {n_top} were bought as hits, not {n_top + 2}"}]})
    summ = run.write({})
    return run, summ, critic_rhos, best


def zero_shot_run(df, pool, seed, start, n_hits, rng):
    run = Run(df, "A4", pool, seed, start, rng)
    header(run, rounds=1, batch=40)
    run.llm(1, "director", "Zero-shot arm: explore once, then order all 40 lookups with no feedback. I will lean on "
                           "narrow pores, open metal sites and low MACE S, because I cannot check any of them.",
            n_messages=3)
    r1, d1 = run.q_pool({"cost_tier_max": 2}, "S_mace", True, 25)
    r2, d2 = run.q_pool({"oms": True, "pld_max": 6.0}, "pld_A", True, 25)
    cids = run.tools_parallel(1, "director", [
        ("query_pool", {"filters": {"cost_tier_max": 2}, "sort_by": "S_mace", "ascending": True, "limit": 25}, r1,
         "25 rows by S_mace", None),
        ("query_pool", {"filters": {"oms": True, "pld_max": 6.0}, "sort_by": "pld_A", "ascending": True, "limit": 25},
         r2, f"{len(r2['rows'])} rows with OMS, PLD ≤ 6 Å", None)])
    cand = list(d1["id"]) + list(d2["id"])
    picks = run.choose(cand, 40, n_hits)
    run.llm(1, "proposer", "Forty picks at once; P(hit) held low because nothing has been checked.", schema="Batch",
            finish="stop", n_messages=5)
    objs = [{"id": m, "p_hit": run.p_hit_for(m, 0.1), "rationale": "descriptor and MACE prior",
             "evidence": [cids[0] if m in set(d1["id"]) else cids[1]]} for m in picks]
    run.emit(1, "proposer", "proposal", {"picks": objs, "stage": "initial", "replaced": []})
    run.emit(1, "human", "human", {"action": "auto", "ids": picks, "note": "recorded run: batch auto-approved"})
    c, _ = run.buy(1, picks, "zero-shot batch of 40", [o["p_hit"] for o in objs])
    run.llm(5, "reporter", "Short report.", strength="low", finish="stop", n_messages=6, completion=200)
    nh = int(run.pool_df.loc[run.bought, "hit"].sum())
    run.emit(5, "reporter", "report", {"source": "model", "written_by": "Muse Glimmer 30B (fixture)", "text": f"Zero-shot campaign: 40 lookups ordered at once, {nh} hits [{c}]. "
                                                f"Picks came from the MACE and narrow-pore lists [{cids[0]}] [{cids[1]}]."})
    run.emit(5, "system", "claimcheck", {"n_claims": 3, "verified": 3, "flagged": []})
    summ = run.write({})
    return run, summ


# --------------------------------------------------------------------------- baselines + results

def curve_from_flags(flags):
    c = [0.0]
    for f in flags:
        c.append(c[-1] + (1 if f else 0))
    return c + [c[-1]] * (41 - len(c))


def arm_stats(arm, label, kind, flag_lists, base_rate, rng):
    curves = np.array([curve_from_flags(f) for f in flag_lists])
    hits = curves[:, 40]
    n = len(hits)
    boots = [hits[rng.integers(0, n, n)].mean() for _ in range(2000)]
    cb = np.array([curves[rng.integers(0, n, n)].mean(0) for _ in range(400)])
    firsts = [next((i + 1 for i, f in enumerate(fl) if f), 41) for fl in flag_lists]
    hm = float(hits.mean())
    return {
        "arm": arm, "label": label, "kind": kind, "n_runs": int(n),
        "hits_mean": round(hm, 3), "hits_ci": [round(float(np.percentile(boots, 2.5)), 3),
                                               round(float(np.percentile(boots, 97.5)), 3)],
        "enrichment": round(hm / 40 / base_rate, 3),
        "lookups_per_hit": round(40 / hm, 2) if hm > 0 else None,
        "first_hit_mean": round(float(np.mean(firsts)), 2),
        "curve": [round(float(x), 3) for x in curves.mean(0)],
        "curve_lo": [round(float(x), 3) for x in np.percentile(cb, 2.5, axis=0)],
        "curve_hi": [round(float(x), 3) for x in np.percentile(cb, 97.5, axis=0)],
    }


def baseline_flags(pdf: pd.DataFrame, arm: str, seed: int):
    rng = np.random.default_rng(seed)
    ids = pdf["id"].to_numpy()
    if arm == "A0":
        sel = rng.permutation(len(pdf))[:40]
        return list(pdf["hit"].to_numpy()[sel])
    if arm == "A1":
        tie = rng.uniform(0, 1, len(pdf))
        ok = (pdf["oms"]) & (pdf["pld_A"] >= 3.3) & (pdf["pld_A"] <= 6.0)
        key = np.where(ok, 0, 1) * 10 + pdf["cost_tier"].to_numpy() + tie
        sel = np.argsort(key)[:40]
        return list(pdf["hit"].to_numpy()[sel])
    if arm == "A2":
        tie = rng.uniform(0, 1e-6, len(pdf))
        sel = np.argsort(pdf["S_mace"].to_numpy() + tie)[:40]
        return list(pdf["hit"].to_numpy()[sel])
    if arm == "A3":
        from sklearn.ensemble import RandomForestRegressor
        feats = pdf[["S_mace", "E_CO2_mace", "E_H2O_mace", "pld_A", "lcd_A", "cost_tier", "void_fraction",
                     "density_gcm3"]].assign(oms=pdf["oms"].astype(float)).to_numpy()
        y = pdf["S"].to_numpy()
        bought = list(rng.permutation(len(pdf))[:10])
        for _ in range(3):
            rf = RandomForestRegressor(n_estimators=60, random_state=seed, n_jobs=1, min_samples_leaf=2)
            rf.fit(feats[bought], y[bought])
            rest = np.setdiff1d(np.arange(len(pdf)), bought)
            preds = np.stack([t.predict(feats[rest]) for t in rf.estimators_])
            acq = preds.mean(0) - 1.0 * preds.std(0)
            bought += list(rest[np.argsort(acq)[:10]])
        return list(pdf["hit"].to_numpy()[bought])
    raise ValueError(arm)


def main():
    if (OUT / "runs").exists():
        shutil.rmtree(OUT / "runs")
    (OUT / "runs").mkdir(parents=True)
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    df = load_table()
    rng = np.random.default_rng(SEED)
    base = datetime(2026, 10, 29, 18, 30, tzinfo=timezone.utc)

    runs = []
    critic_pairs = []
    plans = [
        ("A5", "P1", 0, [0, 1, 2, 2], True, True),
        ("A5", "P2", 0, [0, 1, 1, 2], True, False),
        ("A5", "P3", 0, [1, 1, 2, 1], True, False),
        ("A6", "P1", 0, [0, 1, 1, 1], False, False),
        ("A6", "P2", 0, [0, 0, 1, 1], False, False),
    ]
    t = base
    for arm, pool, seed, plan, sur, show in plans:
        r, s, cr, best = agent_run(df, arm, pool, seed, t, plan, np.random.default_rng(rng.integers(1 << 30)),
                                   surrogate=sur)
        critic_pairs += cr
        runs.append((r, s, show))
        t = r.t + timedelta(minutes=3)
    for pool, nh in (("P1", 2), ("P2", 1)):
        r, s = zero_shot_run(df, pool, 0, t, nh, np.random.default_rng(rng.integers(1 << 30)))
        runs.append((r, s, False))
        t = r.t + timedelta(minutes=3)

    index = []
    # Runs produced by the real agent core (`fortyshots run --mode mock`), copied verbatim minus cassettes.
    import sys
    engine_dirs = [Path(a) for a in sys.argv[1:]]
    for ed in engine_dirs:
        for d in sorted(ed.iterdir()):
            if not (d / "events.jsonl").exists() or not (d / "summary.json").exists():
                continue
            shutil.copytree(d, OUT / "runs" / d.name, ignore=shutil.ignore_patterns("cassette.jsonl", "pending.json",
                                                                                    "human.jsonl"), dirs_exist_ok=True)
            sm = json.loads((d / "summary.json").read_text())
            first = json.loads((d / "events.jsonl").read_text().split("\n", 1)[0])
            index.append({"run_id": d.name, "arm": sm["arm"], "pool": sm["pool"], "seed": sm["seed"], "mode": "mock",
                          "hits": sm["hits"], "spent": sm["spent"], "duration_s": sm["duration_s"], "started": first["t"],
                          "model": sm.get("model")})
            print(f"{d.name}: engine run copied ({sm['hits']} hits)")
    for r, s, show in runs:
        e = {"run_id": r.run_id, "arm": r.arm, "pool": r.pool, "seed": r.seed, "mode": "mock", "hits": s["hits"],
             "spent": s["spent"], "duration_s": s["duration_s"],
             "started": r.start.isoformat(timespec="seconds").replace("+00:00", "Z"), "model": MODEL["name"]}
        if show:
            e["showcase"] = True
        index.append(e)
    (OUT / "runs" / "index.json").write_text(json.dumps(index, indent=1))

    # ---------------- pools.json
    pools = {}
    for p in ["P0", "P1", "P2", "P3", "P4", "P5"]:
        d = df[df["pool"] == p].sort_values("id")
        pools[p] = [{"id": r.id, "S_mace": round(float(r.S_mace), 4), "E_CO2_mace": round(float(r.E_CO2_mace), 4),
                     "E_H2O_mace": round(float(r.E_H2O_mace), 4), "pld_A": float(r.pld_A), "lcd_A": float(r.lcd_A),
                     "oms": bool(r.oms), "metals": r.metals, "cost_tier": int(r.cost_tier), "hit": bool(r.hit),
                     "E_CO2": round(float(r.E_CO2), 4), "E_H2O": round(float(r.E_H2O), 4)} for r in d.itertuples()]
    (OUT / "results" / "pools.json").write_text(json.dumps({"pools": pools}, separators=(",", ":")))

    # ---------------- results.json
    train = df[df["split"] == "train"]
    base_rate = float(train["hit"].mean())
    test_pools = ["P1", "P2", "P3", "P4", "P5"]
    arms = []
    srng = np.random.default_rng(7)
    labels = {"A0": "Random", "A1": "Heuristic", "A2": "MACE-greedy", "A3": "RF-UCB"}
    for arm in ["A0", "A1", "A2", "A3"]:
        seeds = range(50) if arm != "A3" else range(6)
        fl = [baseline_flags(df[df["pool"] == p].reset_index(drop=True), arm, s) for p in test_pools for s in seeds]
        arms.append(arm_stats(arm, labels[arm], "baseline", fl, base_rate, srng))
    llm_labels = {"A4": "Glimmer zero-shot", "A5": "Glimmer agent", "A6": "Agent, no surrogate"}
    by_arm: dict[str, list] = {}
    for r, s, _ in runs:
        by_arm.setdefault(r.arm, []).append(s["hit_flags"])
    for arm in ["A4", "A5", "A6"]:
        arms.append(arm_stats(arm, llm_labels[arm], "llm", by_arm[arm], base_rate, srng))
    am = {a["arm"]: a for a in arms}

    def test(name, a, b):
        diff = am[a]["hits_mean"] - am[b]["hits_mean"]
        lo = diff - (am[a]["hits_ci"][1] - am[a]["hits_ci"][0]) / 2 - (am[b]["hits_ci"][1] - am[b]["hits_ci"][0]) / 2
        hi = diff + (am[a]["hits_ci"][1] - am[a]["hits_ci"][0]) / 2 + (am[b]["hits_ci"][1] - am[b]["hits_ci"][0]) / 2
        return {"name": name, "diff": round(diff, 3), "ci": [round(lo, 3), round(hi, 3)],
                "p": round(float(np.clip(0.5 - diff / 6, 0.001, 0.99)), 3)}

    a5 = [s for r, s, _ in runs if r.arm == "A5"]
    ps = sum((s["p_hits"] for s in a5), [])
    fs = sum((s["hit_flags"] for s in a5), [])
    bins = []
    edges = [0, 0.1, 0.2, 0.3, 0.5, 1.01]
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [(p, f) for p, f in zip(ps, fs) if lo <= p < hi]
        if sel:
            bins.append({"p_mean": round(float(np.mean([p for p, _ in sel])), 3),
                         "hit_rate": round(float(np.mean([f for _, f in sel])), 3), "n": len(sel)})
    ceilings = []
    for p in test_pools:
        d = df[df["pool"] == p].copy()
        d["S_init"] = d["E_CO2_init"] + np.maximum(0, d["E_CO2_init"] - d["E_H2O_init"] + 0.1)
        ceilings.append({"pool": p, "oracle_hits": int(min(40, d["hit"].sum())),
                         "dft_init_hits": int(d.sort_values("S_init").head(40)["hit"].sum()),
                         "random_expected": round(40 * float(d["hit"].mean()), 2)})
    cc = [e["payload"] for r, _, _ in runs for e in r.events if e["kind"] == "claimcheck"]
    results = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "mock",
        "prereg_sha": "mock000",
        "budget": 40,
        "base_rate": round(base_rate, 5),
        "pools": [{"pool": p, "n": int((df["pool"] == p).sum()), "hits": int(df[df["pool"] == p]["hit"].sum())}
                  for p in test_pools],
        "arms": arms,
        "tests": [test("A5 − A3 (primary)", "A5", "A3"), test("A5 − A0", "A5", "A0"), test("A5 − A6", "A5", "A6")],
        "calibration": {"brier": round(float(np.mean([(p - f) ** 2 for p, f in zip(ps, fs)])), 4), "n": len(ps),
                        "bins": bins},
        "critic": {"visual_rho_mae": round(float(np.mean([abs(a - b) for a, b in critic_pairs])), 3),
                   "n": len(critic_pairs)},
        "honesty": {"mace_auc": 0.49, "mace_auc_ci": [0.40, 0.58], "gas_relax_auc": 0.32, "dft_init_auc": 0.69,
                    "mace_mae_dft_geom": 0.159, "eqv2_mae_dft_geom": 0.146,
                    "notes": "Fixture: honesty values copied from the research measurements in PLAN §5; "
                             "everything else in this file is mock."},
        "leak": {"L_auc": 0.51, "L2_auc": 0.48},
        "claims": {"n": int(sum(c["n_claims"] for c in cc)), "verified": int(sum(c["verified"] for c in cc)),
                   "flagged": int(sum(len(c["flagged"]) for c in cc))},
        "ceilings": ceilings,
    }
    (OUT / "results" / "results.json").write_text(json.dumps(results, indent=1))
    for r, s, show in runs:
        print(f"{r.run_id}: {len(r.events)} events, hits {s['hits']}, {s['duration_s']} s{' (showcase)' if show else ''}")
    print("arms:", {a["arm"]: a["hits_mean"] for a in arms})


if __name__ == "__main__":
    main()
