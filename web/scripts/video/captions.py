#!/usr/bin/env python3
"""Captions and timing for the demo video (stdlib only).

    python3 web/scripts/video/captions.py [--voice media/voice.m4a] [--values media/values.json]
                                          [--set KEY=VALUE ...] [--variant auto|A|B] [--draft]

Reads the storyboard table in VIDEO.md and the 40-second clip table in LAUNCH.md, fills every {{PLACEHOLDER}}
and writes, into media/ (or --out-dir):

  captions.srt   the narration as subtitles, on the final video's clock
  x-clip.srt     the captions of the 40 s X clip (LAUNCH.md), 1080x1920
  timeline.json  everything assemble.sh needs: row start/end on the final clock, where each row's narration
                 sits in voice.m4a and in the video, card texts, lower third, URLs, unresolved placeholders

Timing. Picture rows start at their storyboard timestamps. Each row's narration starts 0.5 s into its row.
Without a voice track the narration length is estimated (about 144 words a minute, a pause between sentences).
With --voice, ffmpeg's silencedetect finds the speech segments in voice.m4a and the sentences are aligned to
them in order (dynamic programming over the pauses, preferring long pauses as sentence and row boundaries).
When a row's narration runs past the next row's storyboard start, that next row (and only as much as needed)
starts later so picture and voice stay together; later rows catch up on their own slack. If the narration
cannot end before the cap (3:00 by default) the script exits with an error rather than writing a long cut.

Placeholder values, in order of precedence: --set, --values JSON, results/results.json (and git for
N_COMMITS), then, with --draft only, the showcase run's own ledger (marked provisional). Anything still
missing is written as [KEY] and listed in timeline.json `unresolved`.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PH = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*(?::[^}]*)?\}\}")
WPS = 2.4  # words per second for estimates (~144 wpm, a calm read)
SENT_PAUSE = 0.35
ROW_LEAD = 0.5
ROW_GAP = 0.25
LINE = 42  # characters per caption line (two lines max)


# ----------------------------------------------------------------------------------------------- parsing
def ts(s: str) -> float:
    s = s.strip()
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    return float(s)


def table_rows(md: str, header_word: str) -> list[list[str]]:
    """Rows (cells) of the first markdown table whose header row contains `header_word`, after `start`."""
    rows: list[list[str]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|") and header_word in ln and i + 1 < len(lines) and re.match(r"^\|[\s\-|:]+\|\s*$", lines[i + 1]):
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                rows.append(cells)
                j += 1
            return rows
        i += 1
    return rows


def section(md: str, title_re: str) -> str:
    m = re.search(rf"^##\s+{title_re}.*?$(.*?)(?=^##\s|\Z)", md, re.M | re.S)
    return m.group(1) if m else ""


def quoted(cell: str) -> list[str]:
    return re.findall(r'"([^"]+)"', cell)


# ----------------------------------------------------------------------------------------------- values
def fmt_count(v) -> str:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "—"
    return str(round(v)) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def showcase_run() -> tuple[dict | None, list[dict]]:
    for base in (ROOT / "runs", ROOT / "docs" / "data" / "runs"):
        idx = load_json(base / "index.json")
        if not idx:
            continue
        r = next((x for x in idx if x.get("showcase")), None) or next(
            (x for x in idx if x.get("arm") == "A5" and x.get("pool") == "P1"), None
        ) or (idx[0] if idx else None)
        if not r:
            continue
        ev_path = base / r["run_id"] / "events.jsonl"
        if ev_path.exists():
            ev = [json.loads(line) for line in ev_path.read_text().splitlines() if line.strip()]
            return r, ev
    return None, []


def derived_values(draft: bool) -> tuple[dict, dict]:
    """Values from results.json (+ git, site config); with draft, provisional ones from the showcase run."""
    vals: dict[str, str] = {}
    prov: dict[str, str] = {}
    res = load_json(ROOT / "results" / "results.json") or load_json(ROOT / "docs" / "data" / "results" / "results.json") or {}
    arms = {a.get("arm"): a for a in res.get("arms", [])}

    def arm(k):
        return arms.get(k)

    if arm("A5"):
        a5 = arm("A5")
        vals["H"] = fmt_count(a5.get("hits_mean"))
        vals["A5_HITS"] = fmt_count(a5.get("hits_mean"))
        lo, hi = (a5.get("hits_ci") or [None, None])[:2]
        vals["A5_LO"], vals["A5_HI"] = fmt_count(lo), fmt_count(hi)
        if a5.get("n_runs") is not None:
            vals["A5_N"] = str(a5["n_runs"])
    for k, key in (("A0", "RANDOM_HITS"), ("A2", "A2_HITS"), ("A3", "A3_HITS")):
        if arm(k):
            vals[key] = fmt_count(arm(k).get("hits_mean"))
    if arm("A3"):
        lo, hi = (arm("A3").get("hits_ci") or [None, None])[:2]
        vals["A3_LO"], vals["A3_HI"] = fmt_count(lo), fmt_count(hi)

    def test(a, b):
        for t in res.get("tests", []):
            n = t.get("name", "").replace(" ", "")
            if n.startswith(f"{a}-{b}") or n.startswith(f"{a}−{b}"):
                return t
        return None

    def verdict(t):
        lo, hi = (t.get("ci") or [0, 0])[:2]
        return "beats" if lo > 0 else "loses" if hi < 0 else "ties"

    if t := test("A5", "A0"):
        d = t.get("diff", 0)
        lo, hi = t.get("ci", [None, None])[:2]
        word = "more" if d >= 0 else "fewer"
        vals["VS_RANDOM"] = f"finds {abs(d):.1f} {word} hits, 95% interval {lo:.1f} to {hi:.1f}"
    if t := test("A5", "A2"):
        vals["VS_A2"] = verdict(t)
    if t := test("A5", "A3"):
        vals["VS_RF"] = verdict(t)
    leak = res.get("leak") or {}
    if leak.get("L_auc") is not None:
        vals["LEAK_SENTENCE"] = f"a refcode-ranking AUC of {leak['L_auc']:.2f}"
    claims = res.get("claims") or {}
    if claims:
        vals["CLAIMS_N"] = str(claims.get("n"))
        vals["CLAIMS_VERIFIED"] = str(claims.get("verified"))
        vals["CLAIMS_FLAGGED"] = str(claims.get("flagged"))
    hon = res.get("honesty") or {}
    if hon.get("mace_auc") is not None:
        vals["MACE_AUC"] = f"{hon['mace_auc']:.2f}"

    try:
        n = subprocess.run(
            ["git", "rev-list", "--count", "hackathon-start..HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        if n:
            vals["N_COMMITS"] = n
    except (OSError, subprocess.CalledProcessError):
        pass
    site = load_json(ROOT / "web" / "site.config.json") or {}
    if site.get("SITE_URL"):
        vals["SITE_URL"] = site["SITE_URL"]
    if site.get("REPO_URL"):
        vals["REPO_URL"] = site["REPO_URL"]

    if draft:
        run, ev = showcase_run()
        if run:
            if "H" not in vals and run.get("hits") is not None:
                prov["H"] = str(run["hits"])
            cc = next((e for e in ev if e.get("kind") == "claimcheck"), None)
            if cc and "CLAIMS_N" not in vals:
                p = cc["payload"]
                prov["CLAIMS_N"] = str(p.get("n_claims"))
                prov["CLAIMS_VERIFIED"] = str(p.get("verified"))
                prov["CLAIMS_FLAGGED"] = str(len(p.get("flagged") or []))
    return vals, prov


def run_network() -> str | None:
    _, ev = showcase_run()
    h = next((e for e in ev if e.get("kind") == "header"), None)
    return (h or {}).get("payload", {}).get("network")


class Filler:
    def __init__(self, values: dict[str, str]):
        self.values = values
        self.unresolved: set[str] = set()
        self.used: set[str] = set()

    def __call__(self, text: str) -> str:
        def sub(m):
            k = m.group(1)
            if k in self.values and self.values[k] not in (None, ""):
                self.used.add(k)
                return str(self.values[k])
            self.unresolved.add(k)
            return f"[{k}]"

        return PH.sub(sub, text)


# ----------------------------------------------------------------------------------------------- text
def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"'])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def spoken_words(text: str) -> float:
    n = 0.0
    for w in text.split():
        core = w.strip(".,;:!?()\"'")
        if re.fullmatch(r"[\d.]+", core):
            n += 1 + core.count(".") * 2 + max(0, len(core.replace(".", "")) - 2) * 0.5
        elif re.fullmatch(r"\[[A-Z0-9_]+\]", core):
            n += 2  # an unfilled placeholder: assume a short number or phrase
        elif core.isupper() and len(core) > 1:
            n += len(core) * 0.5  # spelled out: DFT, API, CO2
        else:
            n += 1
    return n


def est_duration(text: str) -> float:
    return max(0.8, spoken_words(text) / WPS)


def wrap2(text: str, width: int = LINE) -> str:
    """Wrap to at most two balanced lines when it fits; otherwise greedy lines."""
    if len(text) <= width:
        return text
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= width and len(b) <= width:
            score = abs(len(a) - len(b))
            if best is None or score < best[0]:
                best = (score, a + "\n" + b)
    if best:
        return best[1]
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "\n".join(lines)


def cue_chunks(sentence: str, max_chars: int = 2 * LINE) -> list[str]:
    """Split a long sentence into caption-sized chunks: balanced, preferring breaks after punctuation
    or before a conjunction."""
    if len(sentence) <= max_chars:
        return [sentence]
    words = sentence.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if re.search(r"[,;:]$", words[i - 1]):
            score -= 25
        if words[i].lower() in ("and", "but", "so", "which", "because", "or", "then", "against"):
            score -= 10
        if best is None or score < best[0]:
            best = (score, a, b)
    return cue_chunks(best[1], max_chars) + cue_chunks(best[2], max_chars)


def srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    out = []
    for i, (a, b, text) in enumerate(cues, 1):
        out.append(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{wrap2(text)}\n")
    path.write_text("\n".join(out), encoding="utf-8")


# ----------------------------------------------------------------------------------------------- voice
def ffbin(name: str) -> str:
    env = {"ffmpeg": "FFMPEG", "ffprobe": "FFPROBE"}[name]
    import os

    return os.environ.get(env) or shutil.which(name) or name


def media_duration(path: Path) -> float:
    out = subprocess.run(
        [ffbin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def speech_segments(path: Path, noise_db: float | None, min_sil: float) -> tuple[list[tuple[float, float]], float, float]:
    dur = media_duration(path)
    if noise_db is None:
        # threshold relative to the recording's level: 18 dB under its mean volume, clamped
        r = subprocess.run([ffbin("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        mean = float(m.group(1)) if m else -30.0
        noise_db = max(-55.0, min(-28.0, mean - 18.0))
    r = subprocess.run(
        [ffbin("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    sil = []
    start = None
    for line in r.stderr.splitlines():
        if m := re.search(r"silence_start:\s*(-?[\d.]+)", line):
            start = max(0.0, float(m.group(1)))
        elif m := re.search(r"silence_end:\s*([\d.]+)", line):
            sil.append((start if start is not None else 0.0, float(m.group(1))))
            start = None
    if start is not None:
        sil.append((start, dur))
    segs, t = [], 0.0
    for a, b in sil:
        if a > t:
            segs.append((t, a))
        t = max(t, b)
    if t < dur:
        segs.append((t, dur))
    segs = [(a, b) for a, b in segs if b - a >= 0.08]
    merged: list[tuple[float, float]] = []
    for a, b in segs:
        if merged and a - merged[-1][1] < 0.12:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged, dur, noise_db


def align(segs: list[tuple[float, float]], weights: list[float], row_break_after: set[int]) -> list[tuple[float, float]]:
    """Map n sentences (in reading order) onto speech segments. Returns (start, end) per sentence."""
    n, m = len(weights), len(segs)
    if n == 0:
        return []
    if m == 0:
        return [(0.0, 0.0)] * n
    seg_d = [b - a for a, b in segs]
    total_speech = sum(seg_d)
    wsum = sum(weights)
    expect = [w / wsum * total_speech for w in weights]
    if m >= n:
        pref = [0.0]
        for d in seg_d:
            pref.append(pref[-1] + d)
        gap_after = [segs[k + 1][0] - segs[k][1] if k + 1 < m else 0.0 for k in range(m)]
        INF = float("inf")
        # dp[i][k]: best cost with sentences < i assigned to segments < k (sentence i-1 ends at segment k-1)
        dp = [[INF] * (m + 1) for _ in range(n + 1)]
        back = [[-1] * (m + 1) for _ in range(n + 1)]
        dp[0][0] = 0.0
        for i in range(1, n + 1):
            beta = 1.2 if (i - 1) in row_break_after else 0.6
            for k in range(i, m - (n - i) + 1):
                best, arg = INF, -1
                for j in range(i - 1, k):
                    if dp[i - 1][j] == INF:
                        continue
                    d = pref[k] - pref[j]
                    c = math.log(max(d, 0.05) / expect[i - 1]) ** 2
                    if i < n:
                        c -= beta * math.log1p(gap_after[k - 1])
                    v = dp[i - 1][j] + c
                    if v < best:
                        best, arg = v, j
                dp[i][k], back[i][k] = best, arg
        out = [(0.0, 0.0)] * n
        k = m
        for i in range(n, 0, -1):
            j = back[i][k]
            out[i - 1] = (segs[j][0], segs[k - 1][1])
            k = j
        return out
    # fewer pauses than sentences: spread sentences over the speech time by weight
    cum, acc = [], 0.0
    for w in weights:
        acc += w / wsum * total_speech
        cum.append(acc)

    def at(x: float) -> float:
        for (a, b), d in zip(segs, seg_d):
            if x <= d:
                return a + x
            x -= d
        return segs[-1][1]

    out, prev = [], 0.0
    for c in cum:
        out.append((at(prev), at(c)))
        prev = c
    return out


# ----------------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video-md", default=str(ROOT / "VIDEO.md"))
    ap.add_argument("--launch-md", default=str(ROOT / "LAUNCH.md"))
    ap.add_argument("--voice", default=None, help="voice track (m4a/wav); omit for estimated timing")
    ap.add_argument("--values", default=None, help="JSON {KEY: value} for placeholders (default media/values.json if present)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--variant", default="auto", choices=["auto", "A", "B"], help="narration variant at 1:15")
    ap.add_argument("--draft", action="store_true", help="fill missing numbers from the showcase run (provisional)")
    ap.add_argument("--max-seconds", type=float, default=180.0, help="hard cap for the main video")
    ap.add_argument("--noise-db", type=float, default=None, help="silencedetect threshold (default: 18 dB under the mean)")
    ap.add_argument("--min-silence", type=float, default=0.28)
    ap.add_argument("--wifi-off", default="auto", choices=["auto", "yes", "no"], help='X clip: add "Wi-Fi off" (auto: the showcase header says network: off)')
    ap.add_argument("--out-dir", default=str(ROOT / "media"))
    a = ap.parse_args()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    video_md = Path(a.video_md).read_text(encoding="utf-8")
    launch_md = Path(a.launch_md).read_text(encoding="utf-8")

    values, provisional = derived_values(a.draft)
    vpath = Path(a.values) if a.values else out / "values.json"
    if vpath.exists():
        values.update({k: str(v) for k, v in (load_json(vpath) or {}).items() if v is not None and not k.startswith("_")})
    for kv in a.set:
        k, _, v = kv.partition("=")
        values[k.strip()] = v
    for k, v in provisional.items():
        values.setdefault(k, v)
    provisional = {k: v for k, v in provisional.items() if values.get(k) == v}

    variant = a.variant
    if variant == "auto":
        variant = "A" if values.get("WHAT_IT_USED") else "B" if values.get("N_CHANGED") and values.get("A5_N") else "A"
    fill = Filler(values)

    # ---------------------------------------------------------------- storyboard
    sb = table_rows(section(video_md, "Storyboard"), "Narration")
    rows = []
    for cells in sb:
        if len(cells) < 3:
            continue
        m = re.match(r"(\d+:\d\d)\s*[–-]\s*(\d+:\d\d)", cells[0])
        if not m:
            continue
        picture = " | ".join(cells[1:-1])
        narr_cell = cells[-1]
        if "Variant A" in narr_cell:
            vs = dict(re.findall(r'Variant\s+([AB])[^:"]*:\s*"([^"]+)"', narr_cell))
            narration = vs.get(variant) or next(iter(vs.values()), "")
        else:
            narration = " ".join(quoted(narr_cell))
        rows.append({"sb": [ts(m.group(1)), ts(m.group(2))], "picture": picture, "narration_raw": narration})
    if not rows:
        print("no storyboard rows found in VIDEO.md", file=sys.stderr)
        return 1

    for r in rows:
        r["narration"] = fill(r["narration_raw"])
        r["sentences"] = [{"text": s} for s in sentences(r["narration"])]

    # cards and lower third come from the picture column
    title_lines = [fill(q) for q in quoted(rows[0]["picture"])]
    lower = next((fill(q) for q in quoted(rows[-1]["picture"]) if "commits" in q or "Built" in q), None)

    # ---------------------------------------------------------------- voice or estimate
    flat = [s for r in rows for s in r["sentences"]]
    voice_info = None
    if a.voice:
        vp = Path(a.voice)
        if not vp.exists():
            print(f"voice file not found: {vp}", file=sys.stderr)
            return 1
        segs, vdur, noise = speech_segments(vp, a.noise_db, a.min_silence)
        breaks, k = set(), 0
        for r in rows:
            k += len(r["sentences"])
            breaks.add(k - 1)
        spans = align(segs, [est_duration(s["text"]) for s in flat], breaks)
        for s, (x, y) in zip(flat, spans):
            s["src"] = [round(x, 3), round(y, 3)]
        voice_info = {"file": str(vp), "duration": round(vdur, 3), "segments": len(segs), "noise_db": noise,
                      "method": "dp" if len(segs) >= len(flat) else "proportional"}
        # each row's chunk of the voice file
        prev_out = 0.0
        for i, r in enumerate(rows):
            if not r["sentences"]:
                continue
            first, last = r["sentences"][0]["src"][0], r["sentences"][-1]["src"][1]
            nxt = next((rr["sentences"][0]["src"][0] for rr in rows[i + 1:] if rr["sentences"]), vdur)
            src_in = max(prev_out, first - 0.15, 0.0)
            src_out = min(nxt - 0.05, last + 0.3, vdur)
            r["voice_src"] = [round(src_in, 3), round(src_out, 3)]
            prev_out = src_out
    else:
        for r in rows:
            t = 0.0
            for s in r["sentences"]:
                d = est_duration(s["text"])
                s["rel"] = [round(t, 3), round(t + d, 3)]
                t += d + SENT_PAUSE

    # ---------------------------------------------------------------- place rows on the final clock
    cap = min(a.max_seconds, rows[-1]["sb"][1]) - 0.4
    voice_end = 0.0
    starts = []
    for i, r in enumerate(rows):
        start = r["sb"][0] if i == 0 else max(r["sb"][0], voice_end + ROW_GAP)
        starts.append(start)
        if not r["sentences"]:
            continue
        v0 = start + (0.6 if i == 0 else ROW_LEAD)
        if "voice_src" in r:
            src_in = r["voice_src"][0]
            for s in r["sentences"]:
                s["dst"] = [round(v0 + s["src"][0] - src_in, 3), round(v0 + s["src"][1] - src_in, 3)]
            r["voice_dst"] = [round(v0, 3), round(v0 + r["voice_src"][1] - src_in, 3)]
        else:
            for s in r["sentences"]:
                s["dst"] = [round(v0 + s["rel"][0], 3), round(v0 + s["rel"][1], 3)]
            r["voice_dst"] = [round(v0, 3), r["sentences"][-1]["dst"][1]]
        voice_end = r["voice_dst"][1]
    for i, r in enumerate(rows):
        end = starts[i + 1] if i + 1 < len(rows) else cap
        r["dst"] = [round(starts[i], 3), round(end, 3)]
    over = voice_end + 0.3 - cap
    if over > 0:
        print(
            f"ERROR: the narration ends at {voice_end:.1f} s and does not fit under the {a.max_seconds:.0f} s cap "
            f"(over by {over:.1f} s). Shorten pauses in voice.m4a or trim the narration.",
            file=sys.stderr,
        )
        return 2
    for i, r in enumerate(rows):
        if r["dst"][1] - r["dst"][0] < 2.0:
            print(f"ERROR: row {i} ({r['picture'][:40]}…) would last {r['dst'][1] - r['dst'][0]:.1f} s; the narration before it runs too long.", file=sys.stderr)
            return 2

    # ---------------------------------------------------------------- main SRT
    cues = []
    all_s = [s for r in rows for s in r["sentences"]]
    for j, s in enumerate(all_s):
        a0, b0 = s["dst"]
        nxt = all_s[j + 1]["dst"][0] if j + 1 < len(all_s) else cap
        b0 = min(max(b0 + 0.3, a0 + 1.2), nxt - 0.05)
        chunks = cue_chunks(s["text"])
        tot = sum(len(c) for c in chunks)
        t = a0
        for c in chunks:
            d = (b0 - a0) * len(c) / tot
            cues.append((t, t + d - (0.04 if c is not chunks[-1] else 0), c))
            t += d
    write_srt(out / "captions.srt", cues)

    # ---------------------------------------------------------------- X clip (LAUNCH.md)
    wifi = a.wifi_off == "yes" or (a.wifi_off == "auto" and run_network() == "off")
    xrows = []
    for cells in table_rows(section(launch_md, r"40-second clip"), "Caption"):
        m = re.match(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*s", cells[0])
        if not m or len(cells) < 3:
            continue
        cap_cell = cells[-1]
        qs = quoted(cap_cell)
        text = fill(qs[0]) if qs else ""
        if re.search(r"\+\s*site URL", cap_cell):
            text = f"{text} {values.get('SITE_URL', '[SITE_URL]')}".strip()
        if wifi and "Wi-Fi off" in cap_cell and "Wi-Fi" not in text:
            text = text.replace("No cloud.", "No cloud. Wi-Fi off.", 1) if "No cloud." in text else f"Wi-Fi off. {text}"
        xrows.append({"dst": [float(m.group(1)), float(m.group(2))], "picture": cells[1], "caption": text})
    xcues = [(r["dst"][0] + 0.15, r["dst"][1] - 0.1, r["caption"]) for r in xrows if r["caption"]]
    write_srt(out / "x-clip.srt", xcues)

    # ---------------------------------------------------------------- timeline.json
    for r in rows:
        for s in r["sentences"]:
            s.pop("rel", None)
        r.pop("narration_raw", None)
    tl = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "draft": bool(a.draft),
        "total": round(cap, 3),
        "max_seconds": a.max_seconds,
        "variant": variant,
        "voice": voice_info,
        "values": {k: values[k] for k in sorted(fill.used)},
        "provisional": provisional,
        "unresolved": sorted(fill.unresolved),
        "title_card": title_lines,
        "lower_third": lower,
        "urls": {"site": values.get("SITE_URL"), "repo": values.get("REPO_URL")},
        "rows": rows,
        "x_clip": {"total": xrows[-1]["dst"][1] if xrows else 40.0, "wifi_off": wifi, "rows": xrows},
        "srt": {"main": str(out / "captions.srt"), "x_clip": str(out / "x-clip.srt")},
    }
    (out / "timeline.json").write_text(json.dumps(tl, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---------------------------------------------------------------- report
    print(f"captions: {len(cues)} cues -> {out / 'captions.srt'}; x clip: {len(xcues)} cues -> {out / 'x-clip.srt'}")
    how = f"voice, {voice_info['method']} alignment over {voice_info['segments']} speech segments" if voice_info else "estimated (no voice)"
    print(f"timing: {how}; variant {variant}; narration ends {voice_end:.1f} s of {cap:.1f} s")
    for i, r in enumerate(rows):
        shift = r["dst"][0] - r["sb"][0]
        vd = r.get("voice_dst")
        note = f"  starts {shift:+.1f} s vs storyboard" if abs(shift) > 0.05 else ""
        vtxt = f"voice {vd[0]:6.1f}-{vd[1]:6.1f}" if vd else "no narration"
        print(f"  row {i}: {r['dst'][0]:6.1f}-{r['dst'][1]:6.1f}  {vtxt}{note}")
    if provisional:
        print("provisional (draft, from the showcase run): " + ", ".join(f"{k}={v}" for k, v in provisional.items()))
    if fill.unresolved:
        print("unresolved placeholders (shown as [KEY]): " + ", ".join(sorted(fill.unresolved)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
