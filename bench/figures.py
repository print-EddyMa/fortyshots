"""Static figures for the results page -> results/fig/*.png and *.svg.

usage: python -m bench.figures [--root .]
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bench.common import ROOT, TEST_POOLS, load_data

BG = "#fafafa"
FG = "#0a0a0a"
MUTED = "#666666"
RULE = "#e5e5e5"
ACCENT = "#c2410c"
GREY_DARK = "#525252"
GREY = "#737373"
GREY_LIGHT = "#d4d4d4"
PX = 0.75  # 1 px in points at 96 dpi
AGENT = "A5"
SHORT = {"A0": "random", "A1": "heuristic", "A2": "MACE greedy", "A3": "RF-UCB", "A4": "zero-shot",
         "A5": "agent", "A6": "no surrogate"}


def style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans Mono", "font.size": 11 * PX, "axes.labelsize": 11 * PX,
        "xtick.labelsize": 11 * PX, "ytick.labelsize": 11 * PX, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": GREY_LIGHT, "axes.linewidth": PX,
        "figure.facecolor": BG, "axes.facecolor": "none", "savefig.facecolor": BG,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": RULE, "grid.linewidth": PX,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "xtick.major.width": PX, "ytick.major.size": 0, "svg.fonttype": "none",
    })


def save(fig, out, name):
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=192, bbox_inches="tight")
    fig.savefig(out / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def arm_color(arm):
    return ACCENT if arm == AGENT else GREY_DARK if arm in ("A3", "A4", "A6") else GREY


def fig_arms(res, out):
    arms = res["arms"]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = np.arange(len(arms))
    for k, a in enumerate(arms):
        c = arm_color(a["arm"])
        ax.plot([k, k], a["hits_ci"], color=c, lw=2 * PX, solid_capstyle="round")
        ax.plot(k, a["hits_mean"], "o", ms=8 * PX, color=c, mec=BG, mew=2 * PX, zorder=3)
        ax.annotate(f"{a['hits_mean']:.1f}", (k, a["hits_ci"][1]), xytext=(0, 4), textcoords="offset points",
                    ha="center", va="bottom", color=FG if a["arm"] == AGENT else MUTED)
    rand = np.mean([c["random_expected"] for c in res["ceilings"]])
    ax.axhline(rand, color=GREY, lw=PX, ls=(0, (3, 3)))
    ax.annotate(f"random expectation {rand:.2f}", (len(arms) - 0.5, rand), xytext=(0, -3),
                textcoords="offset points", ha="right", va="top", color=MUTED)
    ax.set_xticks(x, [f"{a['arm']}\n{SHORT.get(a['arm'], '')}" for a in arms])
    ax.set_xlim(-0.6, len(arms) - 0.4)
    ax.set_ylim(0, None)
    ax.set_ylabel("hits in 40 lookups (mean, 95% CI)")
    save(fig, out, "arms")


def fig_discovery(res, out):
    arms = {a["arm"]: a for a in res["arms"]}
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    k = np.arange(41)
    for arm, fill in (("A0", "#ececec"), ("A3", "#dcdcdc")):
        if arm in arms:
            a = arms[arm]
            ax.fill_between(k, a["curve_lo"], a["curve_hi"], color=fill, lw=0)
            ax.plot(k, a["curve"], color=arm_color(arm), lw=2 * PX)
            ax.annotate(f"{arm} {a['label']}", (40, a["curve"][-1]), xytext=(4, 0), textcoords="offset points",
                        va="center", color=MUTED)
    if AGENT in arms:
        a = arms[AGENT]
        ax.plot(k, a["curve"], color=ACCENT, lw=2 * PX)
        ax.plot(k, a["curve_lo"], color=ACCENT, lw=PX, ls=(0, (2, 2)))
        ax.plot(k, a["curve_hi"], color=ACCENT, lw=PX, ls=(0, (2, 2)))
        ax.annotate(f"{AGENT} {a['label']}", (40, a["curve"][-1]), xytext=(4, 0), textcoords="offset points",
                    va="center", color=FG)
    ax.set_xlim(0, 40)
    ax.set_ylim(0, None)
    ax.set_xticks([0, 10, 20, 30, 40])
    ax.set_xlabel("DFT lookups")
    ax.set_ylabel("cumulative hits (mean, 95% band)")
    save(fig, out, "discovery")


def fig_honesty(res, data, out):
    df = pd.concat([data.pool(p) for p in TEST_POOLS]).dropna(subset=["S_mace", "S"])
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(4.8, 4.2))
    both = np.r_[df.S_mace, df.S]
    lo, hi = both.min() - 0.1, np.percentile(both, 98)  # keep every hit; clip the long positive tail
    miss = df[~df.hit.astype(bool)]
    hit = df[df.hit.astype(bool)]
    ax.scatter(miss.S_mace.clip(lo, hi), miss.S.clip(lo, hi), s=6, color=GREY_LIGHT, lw=0)
    ax.scatter(hit.S_mace.clip(lo, hi), hit.S.clip(lo, hi), s=14, facecolor=FG, edgecolor=BG, lw=PX)
    ax.plot([lo, hi], [lo, hi], color=GREY, lw=PX, ls=(0, (3, 3)))
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    h = res["honesty"]
    txt = f"MACE hit AUC {h['mace_auc']:.2f} [{h['mace_auc_ci'][0]:.2f}, {h['mace_auc_ci'][1]:.2f}]" \
        if h["mace_auc"] is not None else ""
    n_clip = int(((df.S_mace > hi) | (df.S > hi)).sum())
    clip = f"\n{n_clip} points beyond {hi:.1f} eV drawn at the edge" if n_clip else ""
    ax.annotate(f"{txt}\nn = {len(df)} MOFs, {len(hit)} hits (black){clip}", (0.02, 0.98),
                xycoords="axes fraction", va="top", color=MUTED)
    ax.set_xlabel("S_mace, eV (cheap oracle)")
    ax.set_ylabel("S, eV (ODAC23 DFT)")
    save(fig, out, "honesty")


def fig_reliability(res, out):
    cal = res.get("calibration")
    if not cal:
        return
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.plot([0, 1], [0, 1], color=GREY, lw=PX, ls=(0, (3, 3)))
    p = [b["p_mean"] for b in cal["bins"]]
    r = [b["hit_rate"] for b in cal["bins"]]
    n = np.array([b["n"] for b in cal["bins"]])
    ax.plot(p, r, color=ACCENT, lw=2 * PX)
    ax.scatter(p, r, s=8 + 60 * n / n.max(), color=ACCENT, edgecolor=BG, lw=2 * PX, zorder=3)
    for b in cal["bins"]:
        ax.annotate(f"n={b['n']}", (b["p_mean"], b["hit_rate"]), xytext=(5, -2), textcoords="offset points",
                    color=MUTED, va="top")
    ax.annotate(f"Brier {cal['brier']:.3f}, n = {cal['n']} picks", (0.02, 0.98), xycoords="axes fraction",
                va="top", color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("agent P(hit), bin mean")
    ax.set_ylabel("observed hit rate")
    save(fig, out, "reliability")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args([] if argv is None else argv)
    root = Path(a.root)
    res = json.loads((root / "results" / "results.json").read_text())
    out = root / "results" / "fig"
    style()
    fig_arms(res, out)
    fig_discovery(res, out)
    fig_honesty(res, load_data(root / "data"), out)
    fig_reliability(res, out)
    print("figures ->", out)


if __name__ == "__main__":
    main(sys.argv[1:])
