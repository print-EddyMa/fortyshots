#!/usr/bin/env bash
# Cut the fortyshots demo video and the 40 s X clip from the voice track, the live screen recording and the
# browser B-roll. Works on Linux and on macOS (bash 3.2, Homebrew ffmpeg, python3 from the Command Line Tools).
#
#   web/scripts/video/assemble.sh                         final cut from media/voice.m4a + media/live.mov
#   web/scripts/video/assemble.sh --draft                 B-roll only, silent, provisional numbers
#   web/scripts/video/assemble.sh --live-cuts "4-12,61-69" --variant B
#
# Inputs (defaults in brackets):
#   --voice FILE        narration [media/voice.m4a if present]; without it: estimated timing, silent track
#   --live FILE         Mac screen recording for 0:22-0:38 [media/live.mov or live.mp4]; without it: the
#                       console's Model tab from the B-roll stands in
#   --live-cuts LIST    seconds of live.mov to use, "in-out,in-out" [0-row length]; if they add up to more than
#                       the row, they are sped up and get an "N× speed" label
#   --shots FILE        B-roll manifest [media/broll/shots.json] (record it with record-broll.mjs)
#   --timeline FILE     use this timeline.json instead of running captions.py (e.g. a hand-edited one)
#   --values FILE       placeholder values for captions.py [media/values.json if present]
#   --variant A|B       narration variant at 1:15 [auto]
#   --cuts FILE         per-row footage overrides [media/cuts.json if present]; see plan.py
#   --no-captions       do not burn captions into the main video (the X clip always has them)
#   --no-x | --only-x   skip the X clip / only the X clip
#   --out FILE          main video [media/fortyshots-demo.mp4; with --draft media/draft-demo.mp4]
#   --x-out FILE        X clip [media/fortyshots-x-clip.mp4; with --draft media/draft-x-clip.mp4]
#   --max-seconds N     hard cap for the main video [180]
#   --preset P          x264 preset for the final pass [slow; with --draft veryfast]
#
# Environment: FFMPEG / FFPROBE (binaries), PYTHON (python3), FONT_PYTHON (python with fontTools, for the
# Geist Mono TTFs; default .venv/bin/python).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
MEDIA="$ROOT/media"
PY="${PYTHON:-python3}"

die() { printf '\nassemble: %s\n' "$1" >&2; [ -n "${2:-}" ] && printf '  fix: %s\n' "$2" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------------------------------------- arguments
VOICE=""; LIVE=""; LIVE_CUTS=""; SHOTS="$MEDIA/broll/shots.json"; TIMELINE=""; VALUES=""; VARIANT="auto"
CUTS=""; CAPTIONS="burn"; DO_MAIN=1; DO_X=1; OUT=""; XOUT=""; MAXS=180; PRESET=""; DRAFT=0
VOICE_SET=0; LIVE_SET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --voice) VOICE="$2"; VOICE_SET=1; shift 2 ;;
    --live) LIVE="$2"; LIVE_SET=1; shift 2 ;;
    --live-cuts) LIVE_CUTS="$2"; shift 2 ;;
    --shots) SHOTS="$2"; shift 2 ;;
    --timeline) TIMELINE="$2"; shift 2 ;;
    --values) VALUES="$2"; shift 2 ;;
    --variant) VARIANT="$2"; shift 2 ;;
    --cuts) CUTS="$2"; shift 2 ;;
    --no-captions) CAPTIONS="none"; shift ;;
    --no-x) DO_X=0; shift ;;
    --only-x) DO_MAIN=0; shift ;;
    --out) OUT="$2"; shift 2 ;;
    --x-out) XOUT="$2"; shift 2 ;;
    --max-seconds) MAXS="$2"; shift 2 ;;
    --preset) PRESET="$2"; shift 2 ;;
    --draft) DRAFT=1; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) die "unknown option $1" "web/scripts/video/assemble.sh --help" ;;
  esac
done

if [ "$DRAFT" = 1 ]; then
  : "${OUT:=$MEDIA/draft-demo.mp4}"; : "${XOUT:=$MEDIA/draft-x-clip.mp4}"; : "${PRESET:=veryfast}"
  [ "$VOICE_SET" = 1 ] || VOICE=""
  [ "$LIVE_SET" = 1 ] || LIVE=""
else
  : "${OUT:=$MEDIA/fortyshots-demo.mp4}"; : "${XOUT:=$MEDIA/fortyshots-x-clip.mp4}"; : "${PRESET:=slow}"
  [ "$VOICE_SET" = 1 ] || { [ -f "$MEDIA/voice.m4a" ] && VOICE="$MEDIA/voice.m4a"; } || true
  if [ "$LIVE_SET" = 0 ]; then
    for f in "$MEDIA/live.mov" "$MEDIA/live.mp4"; do [ -f "$f" ] && { LIVE="$f"; break; }; done
  fi
fi
[ -n "$CUTS" ] || { [ -f "$MEDIA/cuts.json" ] && CUTS="$MEDIA/cuts.json"; } || true
[ -z "$VOICE" ] || [ -f "$VOICE" ] || die "voice file not found: $VOICE" "./lab push-media ~/Desktop/voice.m4a, then fetch it (VIDEO.md)"
[ -z "$LIVE" ] || [ -f "$LIVE" ] || die "live recording not found: $LIVE"
case "$VARIANT" in auto|A|B) ;; *) die "--variant must be auto, A or B" ;; esac

# ---------------------------------------------------------------------------------------------- tools
FFMPEG="${FFMPEG:-}"
if [ -z "$FFMPEG" ]; then
  # Homebrew's slim ffmpeg may lack drawtext/libass; ffmpeg-full has both.
  for c in ffmpeg /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg /usr/local/opt/ffmpeg-full/bin/ffmpeg; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -hide_banner -filters 2>/dev/null | grep -q ' drawtext ' \
       && "$c" -hide_banner -filters 2>/dev/null | grep -q ' ass '; then FFMPEG="$c"; break; fi
  done
fi
[ -n "$FFMPEG" ] || die "no ffmpeg with the drawtext and ass filters (needs freetype and libass)" \
  "brew install ffmpeg-full   (or: sudo apt install ffmpeg)"
FFPROBE="${FFPROBE:-$(dirname "$(command -v "$FFMPEG")")/ffprobe}"
[ -x "$FFPROBE" ] || FFPROBE="$(command -v ffprobe || true)"
[ -n "$FFPROBE" ] || die "ffprobe not found" "brew install ffmpeg-full"
command -v "$PY" >/dev/null 2>&1 || die "python3 not found" "xcode-select --install"
export FFMPEG FFPROBE
"$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q libx264 || die "this ffmpeg has no libx264 encoder" "brew install ffmpeg-full"

# ---------------------------------------------------------------------------------------------- fonts
FONTS="$MEDIA/fonts"
FONT="$FONTS/GeistMono-Regular.ttf"; FONT_MED="$FONTS/GeistMono-Medium.ttf"; FAMILY="Geist Mono"; ADV=0.6
if [ ! -f "$FONT" ]; then
  FPY="${FONT_PYTHON:-$ROOT/.venv/bin/python}"
  [ -x "$FPY" ] || FPY="$PY"
  "$FPY" "$HERE/geist_ttf.py" --out "$FONTS" >/dev/null 2>&1 || true
fi
if [ ! -f "$FONT" ]; then
  mkdir -p "$FONTS"
  for f in /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf /System/Library/Fonts/Menlo.ttc /Library/Fonts/Menlo.ttc; do
    if [ -f "$f" ]; then
      FONT="$f"; FONT_MED="$f"
      case "$f" in *Menlo*) FAMILY="Menlo" ;; *) FAMILY="DejaVu Sans Mono" ;; esac
      cp "$f" "$FONTS/" 2>/dev/null || true
      break
    fi
  done
  [ -f "$FONT" ] || die "no monospace font for the captions" "install fontTools in .venv (pip install fonttools) so geist_ttf.py can convert Geist Mono"
  say "fonts: Geist Mono TTF unavailable, using $FAMILY"
fi
[ -f "$FONT_MED" ] || FONT_MED="$FONT"

# ---------------------------------------------------------------------------------------------- B-roll
[ -f "$SHOTS" ] || die "no B-roll manifest at $SHOTS" "node web/scripts/video/record-broll.mjs   (after cd web && npm run build)"

# ---------------------------------------------------------------------------------------------- captions + timing
if [ -z "$TIMELINE" ]; then
  TIMELINE="$MEDIA/timeline.json"
  set -- --out-dir "$MEDIA" --variant "$VARIANT" --max-seconds "$MAXS"
  [ -n "$VOICE" ] && set -- "$@" --voice "$VOICE"
  [ -n "$VALUES" ] && set -- "$@" --values "$VALUES"
  [ "$DRAFT" = 1 ] && set -- "$@" --draft
  say "== captions and timing"
  "$PY" "$HERE/captions.py" "$@" || die "captions.py failed (see above)"
fi
[ -f "$TIMELINE" ] || die "timeline not found: $TIMELINE"

# ---------------------------------------------------------------------------------------------- render
probe() { "$FFPROBE" -v error -show_entries "$1" -of default=nw=1:nk=1 "$2" | head -n 1; }
filesize() { wc -c <"$1" | tr -d ' '; }

render() { # $1 main|x  $2 out  $3 workdir  [extra plan args...]
  local kind="$1" out="$2" work="$3"; shift 3
  rm -rf "$work"; mkdir -p "$work" "$(dirname "$out")"
  set -- --timeline "$TIMELINE" --shots "$SHOTS" --work "$work" --format "$kind" --out "$out" \
    --ffmpeg "$FFMPEG" --font "$FONT" --font-medium "$FONT_MED" --fontsdir "$FONTS" --font-family "$FAMILY" \
    --advance "$ADV" --final-preset "$PRESET" "$@"
  [ -n "$CUTS" ] && set -- "$@" --cuts "$CUTS"
  "$PY" "$HERE/plan.py" "$@" >/dev/null || die "plan.py failed for the $kind video"
  bash "$work/render.sh" || die "ffmpeg failed while rendering $out (script: $work/render.sh)"
}

check() { # $1 file  $2 expected WxH  $3 max seconds
  local f="$1" dur w h size
  dur="$(probe format=duration "$f")"
  w="$(probe stream=width "$f")"; h="$(probe stream=height "$f")"
  size="$(filesize "$f")"
  say "   $(basename "$f"): ${w}x${h}, $(printf '%.2f' "$dur") s, $((size / 1048576)) MB"
  [ "${w}x${h}" = "$2" ] || die "$f is ${w}x${h}, expected $2"
  if awk -v d="$dur" -v m="$3" 'BEGIN { exit !(d > m) }'; then
    mv "$f" "${f%.mp4}.TOO-LONG.mp4"
    die "$(basename "$f") is $dur s, over the $3 s cap. Renamed it to ${f%.mp4}.TOO-LONG.mp4." \
      "trim pauses in the voice track or shorten the narration, then rerun"
  fi
  [ "$size" -le 99614720 ] || say "   warning: over 95 MB; GitHub and some uploaders refuse files that large"
}

if [ "$DO_MAIN" = 1 ]; then
  say "== main video -> $OUT"
  set --
  [ -n "$VOICE" ] && set -- "$@" --voice "$VOICE"
  [ -n "$LIVE" ] && set -- "$@" --live "$LIVE" --live-cuts "$LIVE_CUTS"
  set -- "$@" --captions "$CAPTIONS" --crf 20
  render main "$OUT" "$MEDIA/.work/main" "$@"
  check "$OUT" 1920x1080 "$MAXS"
  cp "$MEDIA/captions.srt" "${OUT%.mp4}.srt" 2>/dev/null || true
fi
if [ "$DO_X" = 1 ]; then
  say "== X clip -> $XOUT"
  render x "$XOUT" "$MEDIA/.work/x" --captions burn --crf 21
  check "$XOUT" 1080x1920 40.5
fi
[ -z "$VOICE" ] && [ "$DO_MAIN" = 1 ] && say "note: no voice track; the audio is silent and caption timing is estimated"
[ -z "$LIVE" ] && [ "$DO_MAIN" = 1 ] && say "note: no live.mov; 0:22-0:38 shows the console's Model tab instead"
"$PY" - "$TIMELINE" <<'EOF'
import json, sys
t = json.load(open(sys.argv[1]))
if t.get("unresolved"):
    print("note: unfilled placeholders shown as [KEY]: " + ", ".join(t["unresolved"]) + "  (fill media/values.json)")
if t.get("provisional"):
    print("note: provisional numbers from the showcase run: " + ", ".join(f"{k}={v}" for k, v in t["provisional"].items()))
EOF
say "done"
