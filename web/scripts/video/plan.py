#!/usr/bin/env python3
"""Edit plan for the demo video (stdlib only). Called by assemble.sh; writes a bash script of ffmpeg commands.

    python3 plan.py --timeline media/timeline.json --shots media/broll/shots.json --work media/.work/main \\
        --format main|x --out media/fortyshots-demo.mp4 [--voice media/voice.m4a] [--live media/live.mov]
        [--live-cuts "12-20,95-103"] [--cuts media/cuts.json] [--captions burn|none] [--preset veryfast]
        --ffmpeg ffmpeg --font media/fonts/GeistMono-Regular.ttf --font-medium ... --fontsdir media/fonts
        --font-family "Geist Mono" --advance 0.6 > render.sh

Each picture row of timeline.json becomes one or more clips (a B-roll shot from shots.json, live.mov, or a text
card), rendered to an intermediate H.264 segment with any overlay that belongs to it (the "8× speed" label).
The segments are joined with straight cuts, then one final pass burns the lower third and the captions and
lays the narration chunks onto the clock (or a silent track).

Which footage goes in which row is picked by keywords in the storyboard's picture column (DEFAULT_MAIN /
DEFAULT_X below). media/cuts.json can replace any row: {"3": [{"shot": "console-8x", "weight": 1}], ...}
(keys are row numbers; items are {"shot", "at" (mark name or seconds), "weight", "max"}, {"card": "title"|"end"}
or {"live": true}).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

FPS = 30
BG = "0x0d0d0d"
FG = "0xededed"
MUTED = "0x8b8b8b"
# VIDEO.md: the live segment is a demo run on P0, and "the video says so in a caption".
LIVE_NOTE = ("Live demo run", "· development pool P0, not part of the benchmark")

# keyword in the picture column -> clips. weight: share of the row; max: cap in seconds; at: mark or seconds.
# Explainer scenes (scenes/*.html, recorded by record-broll.mjs like any shot): humidity, bottleneck, dflash, loop,
# result, split. Console footage stays short and is the 8x clip where it can be.
DEFAULT_MAIN = [
    ("Hook.", [{"shot": "hero-orbit", "at": "relax", "weight": 1}]),
    ("The problem.", [{"shot": "humidity", "weight": 0.55, "max": 8.3}, {"shot": "bottleneck", "weight": 0.5}]),
    ("What it runs on.", [{"live": True, "weight": 0.62}, {"shot": "dflash", "weight": 0.38, "max": 7.0}]),
    ("Built this week.", [{"shot": "built-card", "weight": 1}]),
    ("Watch it work", [
        {"shot": "loop", "weight": 0.15, "max": 7.0},
        {"shot": "console-8x", "at": "play", "weight": 0.21},
        {"shot": "critic-veto", "weight": 0.11, "at": 0.4, "max": 5.0},
        {"shot": "approval-queue", "at": "approve", "weight": 0.06, "max": 3.0},
        {"shot": "dft-reveal", "weight": 0.12, "at": 0.3},
        {"shot": "binding-site", "at": "site", "weight": 0.15},
        {"shot": "atlas-path", "at": "play", "weight": 0.2},
    ]),
    ("Self-check.", [{"shot": "calibrate-oracle", "weight": 1}]),
    ("The honest result.", [{"shot": "result", "weight": 0.72}, {"shot": "claim-check", "at": "claims", "weight": 0.28}]),
    ("The principle.", [{"shot": "split", "weight": 1}]),
    ("The frontier.", [{"shot": "approval-queue", "at": "mutants", "weight": 1}]),
    ("Close.", [{"shot": "close-card", "weight": 1}]),
]
DEFAULT_X = [
    ("Hook orbit", [{"shot": "hero-orbit", "at": "relax", "weight": 1}]),
    ("Scene `humidity`", [{"shot": "humidity", "weight": 1}]),
    ("timelapse", [{"shot": "console-8x", "at": "play", "weight": 0.6}, {"shot": "binding-site", "at": "site", "weight": 0.4}]),
    ("The atlas", [{"shot": "atlas-path", "at": "play", "weight": 1}]),
    ("Scene `result`", [{"shot": "result", "weight": 1}]),
]


def q(x) -> str:
    return shlex.quote(str(x))


def esc_filter_path(p: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument (we cd into the work dir, so keep it relative)."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def wrap(text: str, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


class Plan:
    def __init__(self, a):
        self.a = a
        self.tl = json.loads(Path(a.timeline).read_text())
        self.shots_doc = json.loads(Path(a.shots).read_text()) if a.shots and Path(a.shots).exists() else {"shots": {}}
        self.shots = self.shots_doc.get("shots", {})
        self.broll_dir = Path(a.shots).parent if a.shots else Path(".")
        self.work = Path(a.work)
        self.vertical = a.format == "x"
        self.W, self.H = (1080, 1920) if self.vertical else (1920, 1080)
        self.fmt_key = "mobile" if self.vertical else "desktop"
        self.cmds: list[str] = []
        self.segments: list[str] = []
        self.textfiles: dict[str, str] = {}
        self.notes: list[str] = []
        self.n = 0
        self.t = 0.0  # clock position of the next segment
        self.live_spans: list[tuple[float, float]] = []

    # ------------------------------------------------------------------ helpers
    def textfile(self, text: str) -> str:
        name = f"t{len(self.textfiles):03d}.txt"
        self.textfiles[name] = text
        return name

    def drawtext(self, text, x, y, size, color, font=None, box=False, enable=None) -> str:
        f = font or self.a.font
        parts = [
            f"fontfile={esc_filter_path(f)}",
            f"textfile={self.textfile(text)}",
            "expansion=none",
            f"fontsize={size}",
            f"fontcolor={color}",
            f"x={x}",
            f"y={y}",
        ]
        if box:
            parts += ["box=1", f"boxcolor={BG}", f"boxborderw={max(8, int(size * 0.5))}"]
        if enable:
            parts.append(f"enable='{enable}'")
        return "drawtext=" + ":".join(parts)

    def two_tone(self, left: str, right: str, x: int, y: int, size: int, enable: str) -> list[str]:
        """White + grey text on one opaque background-coloured plate (drawbox, so both halves share it)."""
        adv = self.a.advance * size
        text_w = int(len(f"{left} {right}") * adv)
        padx, top, bottom = int(size * 0.6), int(size * 0.05), int(size * 1.45)
        out = [f"drawbox=x={x - padx}:y={y + top - int(size * 0.35)}:w={text_w + 2 * padx}:h={bottom - top + int(size * 0.35)}:color={BG}@1:t=fill:enable='{enable}'"]
        out.append(self.drawtext(left, x, y, size, FG, enable=enable))
        if right:
            out.append(self.drawtext(right, x + int(len(left + " ") * adv), y, size, MUTED, enable=enable))
        return out

    def speed_label(self, factor: float) -> str:
        txt = f"{int(round(factor))}× speed" if abs(factor - round(factor)) < 0.05 else f"{factor:.1f}× speed"
        size = 30 if not self.vertical else 34
        adv = self.a.advance * size
        tw = int(len(txt) * adv)
        pad = int(size * 0.5)
        if self.vertical:
            x, y = self.W - tw - 56 - pad, self.H - 560
        else:
            x, y = self.W - tw - 64 - pad, self.H - size - 64 - pad
        return self.drawtext(txt, x, y, size, FG, font=self.a.font_medium, box=True)

    def frames(self, dur: float) -> int:
        return max(1, int(round(dur * FPS)))

    def seg_name(self) -> str:
        self.n += 1
        return f"seg{self.n:03d}.mp4"

    def enc(self) -> str:
        return f"-an -c:v libx264 -preset {self.a.preset} -crf 16 -pix_fmt yuv420p -r {FPS} -video_track_timescale 15360"

    # ------------------------------------------------------------------ clips
    def shot_info(self, sid: str):
        s = self.shots.get(sid)
        if not s or s.get("skipped"):
            return None
        f = s.get(self.fmt_key)
        if not f:
            return None
        path = self.broll_dir / f["file"]
        if not path.exists():
            return None
        return s, f, path

    def shot_offset(self, f: dict, at) -> float:
        if at is None:
            return 0.0
        if isinstance(at, (int, float)):
            return float(at)
        return float((f.get("marks") or {}).get(at, 0.0))

    def clip_shot(self, sid: str, at, dur: float) -> None:
        info = self.shot_info(sid)
        if not info:
            self.notes.append(f"missing shot {sid} ({self.fmt_key}); black filler")
            return self.clip_card([], dur)
        s, f, path = info
        start = f.get("trim_in", 0.0) + self.shot_offset(f, at)
        usable = max(0.0, f.get("duration", 0.0) - self.shot_offset(f, at))
        vf = [f"fps={FPS}", f"scale={self.W}:{self.H}:flags=lanczos", "setsar=1", "format=yuv420p"]
        if usable < dur - 0.02:
            vf.append(f"tpad=stop_mode=clone:stop_duration={dur - usable + 0.5:.3f}")
            self.notes.append(f"{sid}: {usable:.1f} s of footage for {dur:.1f} s; last frame held")
        if (s.get("speed") or 1) > 1:
            vf.append(self.speed_label(float(s["speed"])))
        out = self.seg_name()
        self.cmds.append(
            f"$FF -ss {start:.3f} -i {q(path.resolve())} -vf {q(','.join(vf))} -frames:v {self.frames(dur)} {self.enc()} {q(out)}"
        )
        self.segments.append(out)

    def clip_live(self, dur: float) -> None:
        live = self.a.live
        if not live or not Path(live).exists():
            # stand-in until the Mac recording exists
            return self.clip_shot("console-model", "in", dur)
        self.live_spans.append((self.t, self.t + dur))
        cuts = []
        for part in (self.a.live_cuts or "").split(","):
            m = re.match(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*$", part)
            if m and float(m.group(2)) > float(m.group(1)):
                cuts.append((float(m.group(1)), float(m.group(2))))
        if not cuts:
            cuts = [(0.0, dur)]
        total = sum(b - a for a, b in cuts)
        factor = max(1.0, total / dur)
        acc, prev = 0.0, 0
        for a, b in cuts:
            acc += (b - a) / factor
            fr = round(acc * FPS)
            d = (fr - prev) / FPS
            prev = fr
            if d <= 0:
                continue
            vf = [
                f"setpts=(PTS-STARTPTS)/{factor:.5f}",
                f"fps={FPS}",
                f"scale={self.W}:{self.H}:force_original_aspect_ratio=decrease:flags=lanczos",
                f"pad={self.W}:{self.H}:(ow-iw)/2:(oh-ih)/2:color={BG}",
                "setsar=1",
                "format=yuv420p",
            ]
            if factor > 1.05:
                vf.append(self.speed_label(factor))
            out = self.seg_name()
            self.cmds.append(f"$FF -ss {a:.3f} -t {b - a:.3f} -i {q(Path(live).resolve())} -vf {q(','.join(vf))} -frames:v {self.frames(d)} {self.enc()} {q(out)}")
            self.segments.append(out)
        if total < dur - 0.05:
            self.clip_card([], dur - total)  # pad the rest with the background colour
            self.notes.append(f"live cuts cover {total:.1f} s of a {dur:.1f} s row; padded")

    def card_lines(self, which: str) -> list[tuple[str, str]]:
        tl = self.tl
        if which == "title":
            lines = tl.get("title_card") or []
            return [(lines[0], FG)] + [(x, MUTED) for x in lines[1:]] if lines else []
        if which == "end":
            repo = (tl.get("urls") or {}).get("repo") or ""
            site = (tl.get("urls") or {}).get("site") or ""
            short = lambda u: re.sub(r"^https?://", "", u).rstrip("/")  # noqa: E731
            return [(f"git clone {short(repo)} && ./lab verify", FG), (short(site), MUTED)]
        if which == "x_title":
            rows = (tl.get("x_clip") or {}).get("rows") or []
            text = rows[0]["caption"] if rows else ""
            sents = re.split(r"(?<=[.!?])\s+", text.strip())
            if len(sents) > 1:
                return [(" ".join(sents[:-1]), FG), (sents[-1], MUTED)]
            return [(text, FG)]
        return []

    def clip_card(self, lines: list[tuple[str, str]], dur: float) -> None:
        """Background-coloured card with two-tone mono text, left-aligned like the site's headlines."""
        W, H = self.W, self.H
        margin = 96 if self.vertical else 160
        adv = self.a.advance
        if self.vertical:
            size = 68
            width_chars = int((W - 2 * margin) / (adv * size))
            laid = []
            for text, color in lines:
                # one sentence per line where it fits; wrap the rest
                for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
                    for ln in wrap(sent, width_chars):
                        laid.append((ln, color))
        else:
            longest = max((len(t) for t, _ in lines), default=1)
            size = int(min(64, (W - 2 * margin) / (adv * longest)))
            laid = list(lines)
        lh = int(size * 1.35)
        y0 = int(H / 2 - len(laid) * lh / 2)
        vf = []
        for i, (text, color) in enumerate(laid):
            vf.append(self.drawtext(text, margin, y0 + i * lh, size, color, font=self.a.font_medium))
        out = self.seg_name()
        src = f"-f lavfi -i {q(f'color=c={BG}:s={W}x{H}:r={FPS}:d={dur:.3f}')}"
        vfs = ",".join(vf + ["format=yuv420p"]) if vf else "format=yuv420p"
        self.cmds.append(f"$FF {src} -vf {q(vfs)} -frames:v {self.frames(dur)} {self.enc()} {q(out)}")
        self.segments.append(out)

    # ------------------------------------------------------------------ rows
    def row_clips(self, i: int, picture: str, defaults) -> list[dict]:
        if self.a.cuts and Path(self.a.cuts).exists():
            over = json.loads(Path(self.a.cuts).read_text())
            key = ("x" if self.vertical else "main") + f":{i}"
            if key in over:
                return over[key]
            if not self.vertical and str(i) in over:
                return over[str(i)]
        for kw, clips in defaults:
            if kw.lower() in picture.lower():
                return clips
        self.notes.append(f"row {i}: no footage rule matches {picture[:50]!r}; using the landing page")
        return [{"shot": "landing-headline", "weight": 1}]

    def allocate(self, clips: list[dict], dur: float) -> list[float]:
        """Split a row's duration among its clips: fixed caps first, then weights, limited by available footage."""
        n = len(clips)
        alloc = [0.0] * n
        avail = []
        for c in clips:
            if "shot" in c:
                info = self.shot_info(c["shot"])
                if info:
                    _, f, _ = info
                    avail.append(max(0.0, f.get("duration", 0) - self.shot_offset(f, c.get("at"))))
                else:
                    avail.append(0.0)
            else:
                avail.append(float("inf"))
        # cards with only "max" (no weight) are fixed-length
        rest = dur
        for k, c in enumerate(clips):
            if "max" in c and "weight" not in c:
                alloc[k] = min(c["max"], rest)
                rest -= alloc[k]
        free = [k for k, c in enumerate(clips) if "weight" in c]
        for _ in range(4):
            if rest <= 1e-6 or not free:
                break
            wsum = sum(clips[k]["weight"] for k in free)
            nxt, spent = [], 0.0
            for k in free:
                share = rest * clips[k]["weight"] / wsum
                cap = min(avail[k], clips[k].get("max", float("inf"))) - alloc[k]
                take = min(share, cap)
                alloc[k] += take
                spent += take
                if take >= share - 1e-6:
                    nxt.append(k)
            rest -= spent
            free = nxt
        if rest > 1e-3:
            # nothing can absorb it: hold the last clip longer
            alloc[-1] += rest
        # drop clips shorter than half a second (a flash looks like a glitch)
        for k in range(n):
            if 0 < alloc[k] < 0.5 and n > 1:
                j = k - 1 if k > 0 else k + 1
                alloc[j] += alloc[k]
                alloc[k] = 0.0
        return alloc

    def build_rows(self):
        if self.vertical:
            rows = [{"dst": r["dst"], "picture": r["picture"]} for r in self.tl["x_clip"]["rows"]]
            defaults = DEFAULT_X
        else:
            rows = self.tl["rows"]
            defaults = DEFAULT_MAIN
        for i, r in enumerate(rows):
            f0, f1 = round(r["dst"][0] * FPS), round(r["dst"][1] * FPS)
            dur = (f1 - f0) / FPS
            clips = self.row_clips(i, r["picture"], defaults)
            alloc = self.allocate(clips, dur)
            # whole frames, cumulative rounding, so cuts land on the timeline's clock
            acc, prev, q_alloc = 0.0, 0, []
            for d in alloc:
                acc += d
                fr = round(acc * FPS)
                q_alloc.append((fr - prev) / FPS)
                prev = fr
            for c, d in zip(clips, q_alloc):
                if d <= 0:
                    continue
                self.t = f0 / FPS + sum(q_alloc[: clips.index(c)])
                if "card" in c:
                    self.clip_card(self.card_lines(c["card"]), d)
                elif c.get("live"):
                    self.clip_live(d)
                else:
                    self.clip_shot(c["shot"], c.get("at"), d)
        return rows

    # ------------------------------------------------------------------ captions (ASS, sized for this frame)
    def ass(self, srt: Path, skip_before: float) -> str | None:
        if not srt.exists():
            return None
        cues = []
        for block in re.split(r"\n\s*\n", srt.read_text(encoding="utf-8").strip()):
            ls = block.strip().splitlines()
            if len(ls) < 3:
                continue
            m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", ls[1])
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            if b <= skip_before:
                continue
            cues.append((max(a, skip_before), b, " ".join(ls[2:])))
        size = 52 if self.vertical else 40
        margin_v = 300 if self.vertical else 56
        side = 72 if self.vertical else 160
        width_chars = int((self.W - 2 * side) / (self.a.advance * size))

        def t(x):
            cs = int(round(x * 100))
            return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

        out = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.W}",
            f"PlayResY: {self.H}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Cap,{self.a.font_family},{size},&H00EDEDED,&H00EDEDED,&H000D0D0D,&H000D0D0D,0,0,0,0,100,100,0,0,3,{int(size * 0.32)},0,2,{side},{side},{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for a, b, text in cues:
            body = "\\N".join(wrap(text, width_chars))
            out.append(f"Dialogue: 0,{t(a)},{t(b)},Cap,,0,0,0,,{body}")
        name = "captions.ass"
        self.textfiles[name] = "\n".join(out) + "\n"
        return name

    # ------------------------------------------------------------------ final pass
    def final(self, rows) -> None:
        a = self.a
        total = self.tl["x_clip"]["total"] if self.vertical else self.tl["total"]
        concat = "concat.txt"
        self.textfiles[concat] = "".join(f"file '{s}'\n" for s in self.segments)
        vf = []
        # lower third over the last row (main video)
        if not self.vertical and self.tl.get("lower_third"):
            last = rows[-1]["dst"]
            text = self.tl["lower_third"]
            left, _, right = text.partition(" · ")
            size = 30
            x, y = 96, self.H - 56 - 2 * 40 - 120
            en = f"between(t,{last[0] + 0.4:.2f},{last[1]:.2f})"
            vf += self.two_tone(left, ("· " + right) if right else "", x, y, size, en)
        for s0, s1 in self.live_spans:
            size = 30
            x, y = 96, 72
            en = f"between(t,{s0 + 0.3:.2f},{s1:.2f})"
            vf += self.two_tone(LIVE_NOTE[0], LIVE_NOTE[1], x, y, size, en)
        if a.captions == "burn":
            srt = Path(self.tl["srt"]["x_clip" if self.vertical else "main"])
            skip = 0.0
            if self.vertical:
                first = (self.tl["x_clip"]["rows"] or [{}])[0]
                if "Black" in first.get("picture", ""):
                    skip = first["dst"][1]  # the title card already shows that caption
            assf = self.ass(srt, skip)
            if assf:
                vf.append(f"ass={assf}:fontsdir={esc_filter_path(a.fontsdir)}")
        vf.append("format=yuv420p")

        inputs = f"-f concat -safe 0 -i {concat}"
        voice = a.voice if (a.voice and Path(a.voice).exists() and not self.vertical) else None
        afilter = None
        if voice:
            parts = [r for r in self.tl["rows"] if r.get("voice_src") and r.get("voice_dst")]
            if parts:
                inputs += f" -i {q(Path(voice).resolve())}"
                n = len(parts)
                chain = [f"[1:a]aresample=48000,aformat=channel_layouts=mono,asplit={n}" + "".join(f"[v{k}]" for k in range(n))]
                for k, r in enumerate(parts):
                    s0, s1 = r["voice_src"]
                    d = int(round(r["voice_dst"][0] * 1000))
                    ln = s1 - s0
                    chain.append(
                        f"[v{k}]atrim=start={s0:.3f}:end={s1:.3f},asetpts=PTS-STARTPTS,"
                        f"afade=t=in:d=0.03,afade=t=out:st={max(0, ln - 0.06):.3f}:d=0.06,adelay={d}:all=1[c{k}]"
                    )
                chain.append(
                    "".join(f"[c{k}]" for k in range(n))
                    + f"amix=inputs={n}:normalize=0:dropout_transition=0,highpass=f=70,"
                    f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=end={total:.3f},aformat=channel_layouts=stereo[aout]"
                )
                afilter = ";".join(chain)
        if not afilter:
            inputs += f" -f lavfi -t {total:.3f} -i anullsrc=r=48000:cl=stereo"

        fc = f"[0:v]{','.join(vf)}[vout]"
        if afilter:
            fc += ";" + afilter
        self.textfiles["final.filter"] = fc
        amap = "[aout]" if afilter else "1:a"
        rate = "-maxrate 8M -bufsize 16M" if not self.vertical else "-maxrate 6M -bufsize 12M"
        self.cmds.append(
            f"$FF {inputs} -filter_complex_script final.filter -map '[vout]' -map {q(amap)} "
            f"-c:v libx264 -preset {a.final_preset} -crf {a.crf} {rate} -profile:v high -pix_fmt yuv420p -r {FPS} "
            f"-c:a aac -b:a 160k -ar 48000 -ac 2 -t {total:.3f} -movflags +faststart {q(Path(a.out).resolve())}"
        )

    def script(self) -> str:
        rows = self.build_rows()
        self.final(rows)
        head = [
            "#!/usr/bin/env bash",
            "# generated by web/scripts/video/plan.py; run from anywhere",
            "set -euo pipefail",
            f"cd {q(self.work.resolve())}",
            f'FF="{self.a.ffmpeg} -hide_banner -loglevel error -y"',
        ]
        body = []
        for i, c in enumerate(self.cmds):
            label = "final pass" if i == len(self.cmds) - 1 else f"segment {i + 1}/{len(self.cmds) - 1}"
            body.append(f"echo '  {label}'")
            body.append(c)
        return "\n".join(head + body) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--format", choices=["main", "x"], default="main")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice")
    ap.add_argument("--live")
    ap.add_argument("--live-cuts", default="")
    ap.add_argument("--cuts")
    ap.add_argument("--captions", choices=["burn", "none"], default="burn")
    ap.add_argument("--preset", default="veryfast", help="x264 preset for intermediate segments")
    ap.add_argument("--final-preset", default="medium")
    ap.add_argument("--crf", default="20")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--font", required=True)
    ap.add_argument("--font-medium")
    ap.add_argument("--fontsdir", required=True)
    ap.add_argument("--font-family", default="Geist Mono")
    ap.add_argument("--advance", type=float, default=0.6)
    a = ap.parse_args()
    a.font_medium = a.font_medium or a.font
    # render.sh runs inside the work dir: make every path absolute
    a.font, a.font_medium, a.fontsdir = (str(Path(x).resolve()) for x in (a.font, a.font_medium, a.fontsdir))
    for k in ("voice", "live", "cuts", "out"):
        if getattr(a, k):
            setattr(a, k, str(Path(getattr(a, k)).resolve()))
    p = Plan(a)
    p.work.mkdir(parents=True, exist_ok=True)
    text = p.script()
    for name, content in p.textfiles.items():
        (p.work / name).write_text(content, encoding="utf-8")
    (p.work / "render.sh").write_text(text)
    plan = {"segments": p.segments, "notes": p.notes, "W": p.W, "H": p.H}
    (p.work / "plan.json").write_text(json.dumps(plan, indent=1))
    for n in p.notes:
        print(f"  note: {n}", file=sys.stderr)
    print(str(p.work / "render.sh"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
