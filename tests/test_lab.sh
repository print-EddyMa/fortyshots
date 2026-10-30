#!/usr/bin/env bash
# End-to-end test of ./lab with LAB_MOCK=1 (fortyshots mock-llm instead of llama-server).
# Runs in a throwaway copy of the repo whose git remote is a local bare repository, so nothing
# is pushed anywhere real. Works on Linux and macOS.
#
#   tests/test_lab.sh            uses .venv (or LAB_VENV) for the fortyshots CLI
#   exit 0 = pass, 1 = fail, 77 = skipped (no fortyshots CLI yet)
# Campaigns use the small synthetic dataset in tests/fixtures/data (override with FORTYSHOTS_DATA),
# so the test does not depend on the MACE cache being complete.
set -u -o pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
VENV="${LAB_VENV:-$SRC/.venv}"
if [ ! -x "$VENV/bin/fortyshots" ]; then echo "SKIP: no fortyshots CLI in $VENV"; exit 77; fi

T="$(mktemp -d "${TMPDIR:-/tmp}/fortyshots-labtest.XXXXXX")"
R="$T/repo"
export LAB_MOCK=1 LAB_VENV="$VENV" GLIMMER_DIR="$T/glimmer" LAB_PORT="${LAB_TEST_PORT:-18080}" \
  LAB_SERVE_PORT="${LAB_TEST_SERVE_PORT:-17777}" LAB_NO_UPDATE=1 LAB_YES=1 NO_COLOR=1
if [ -z "${FORTYSHOTS_DATA:-}" ] && [ -d "$SRC/tests/fixtures/data" ]; then
  export FORTYSHOTS_DATA="$SRC/tests/fixtures/data"
fi
export GIT_AUTHOR_NAME=labtest GIT_AUTHOR_EMAIL=labtest@example.invalid
export GIT_COMMITTER_NAME=labtest GIT_COMMITTER_EMAIL=labtest@example.invalid

PASS=0; FAIL=0
cleanup() { (cd "$R" 2>/dev/null && ./lab stop >/dev/null 2>&1); rm -rf "$T"; }
trap cleanup EXIT

check() {  # check "name" condition...
  local name="$1"; shift
  if "$@"; then PASS=$((PASS + 1)); echo "  pass  $name"
  else FAIL=$((FAIL + 1)); echo "  FAIL  $name"; [ -f "$T/out" ] && sed 's/^/        | /' "$T/out" | tail -n 30; fi
}
lab() { (cd "$R" && ./lab "$@") > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { cat "$T/rc"; }
out_has() { grep -q -- "$1" "$T/out"; }
last_line() { grep -v '^[[:space:]]*$' "$T/out" | tail -n 1; }
remote_has() { git -C "$T/remote.git" cat-file -e "$1" 2>/dev/null; }

echo "lab test in $T (venv $VENV)"

# --- a throwaway repo with the files ./lab needs; data/results are symlinked, not copied
mkdir -p "$R"
cp "$SRC/lab" "$R/"
cp -R "$SRC/mac" "$R/mac"
for d in data results fortyshots bench web; do [ -e "$SRC/$d" ] && ln -s "$SRC/$d" "$R/$d"; done
printf 'data\nresults\nfortyshots\nbench\nweb\n' > "$T/exclude"
git -C "$R" init -q -b main
cp "$T/exclude" "$R/.git/info/exclude"
git -C "$R" add lab mac
git -C "$R" commit -q -m "test base"
git init -q --bare "$T/remote.git"
git -C "$T/remote.git" symbolic-ref HEAD refs/heads/main
git -C "$R" remote add origin "$T/remote.git"
git -C "$R" push -q origin main 2>/dev/null
git -C "$R" fetch -q origin
printf '{"name":"t","description":"test plan","runs":[{"arm":"A5","pool":"P0","seed":0}]}\n' > "$T/plan.json"

echo "basics"
lab help;                 check "help exits 0" [ "$(rc)" = 0 ]
lab status;               check "status exits 0" [ "$(rc)" = 0 ]
lab download default;     check "download in mock mode does not download" [ "$(rc)" = 0 ]
lab setup --dry-run;      check "setup --dry-run exits 0" [ "$(rc)" = 0 ]
lab bogus;                check "unknown command fails with a next step" [ "$(rc)" = 1 ]; check "  ... and prints Next:" out_has "Next: ./lab help"
lab model nosuch;         check "unknown profile fails with a next step" out_has "Next: ./lab model default"
lab record nosuch;        check "unknown plan fails with a next step" out_has "Next: ./lab record main"
(cd "$R" && LAB_MOCK=0 ./lab model) > "$T/out" 2>&1
if [ "$(uname -s)" != Darwin ]; then check "real model on Linux points to LAB_MOCK=1" out_has "LAB_MOCK=1 ./lab model"; fi

echo "model"
lab model;                check "model starts the mock server" [ "$(rc)" = 0 ]
check "  ... /health answers 200" [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$LAB_PORT/health")" = 200 ]
lab model;                check "model again is a no-op" out_has "already running"

echo "smoke + push"
lab smoke --keep;         check "smoke exits 0" [ "$(rc)" = 0 ]
check "  ... writes doctor/*-smoke.json" sh -c "ls '$R'/doctor/*-smoke.json >/dev/null 2>&1"
check "  ... reports decode speed" out_has "decode speed"
check "  ... last line says pushed" sh -c "grep -v '^[[:space:]]*\$' '$T/out' | tail -n 1 | grep -q '^pushed'"
check "  ... runs-mac has the smoke report" sh -c "git -C '$T/remote.git' ls-tree -r --name-only runs-mac | grep -q 'doctor/.*-smoke.json'"
check "  ... main on the remote is untouched" [ "$(git -C "$T/remote.git" rev-parse main)" = "$(git -C "$R" rev-parse HEAD)" ]

echo "doctor"
lab doctor;               check "doctor exits 0" [ "$(rc)" = 0 ]
DJ="$(find "$R/doctor" -name '2*.json' ! -name '*-smoke.json' ! -name '*-record-*' ! -name '*-rehearse.json' | sort | tail -n 1)"
check "  ... writes doctor/<time>.json" [ -n "$DJ" ]
check "  ... with verdict ok" grep -q '"verdict": "ok"' "$DJ"
check "  ... embeds the fortyshots report" "$VENV/bin/python" -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['fortyshots'] is not None" "$DJ"
check "  ... pushed" sh -c "git -C '$T/remote.git' ls-tree -r --name-only runs-mac | grep -q '$(basename "$DJ")'"

echo "doctor switches profile when swap grows"
# shellcheck disable=SC2016  # the $ expressions belong to the generated script
printf '#!/bin/sh\nf="%s/swapcount"; n=$(cat "$f" 2>/dev/null || echo 0); echo $((n + 1)) > "$f"\nif [ "$n" -eq 0 ]; then echo 0; else echo 4000; fi\n' "$T" > "$T/fakeswap"
chmod +x "$T/fakeswap"
(cd "$R" && ./lab stop >/dev/null 2>&1; LAB_ENFORCE_SWAP=1 LAB_SWAP_CMD="$T/fakeswap" ./lab doctor) > "$T/out" 2>&1; echo $? > "$T/rc"
check "doctor with 4 GB swap growth fails" [ "$(rc)" = 1 ]
check "  ... switches the default profile to lean" [ "$(cat "$GLIMMER_DIR/.lab/profile" 2>/dev/null)" = lean ]
check "  ... asks to download the lean files" out_has "Next: ./lab download lean && ./lab doctor"
check "  ... report says verdict swap" sh -c "grep -l '\"verdict\": \"swap\"' '$R'/doctor/*.json >/dev/null"
rm -f "$GLIMMER_DIR/.lab/profile"

echo "record"
lab record "$T/plan.json"; check "record exits 0" [ "$(rc)" = 0 ]
RUN="$(find "$R/runs" -mindepth 1 -maxdepth 1 -type d -name '*A5-P0-s0-*' | head -n 1 | sed 's|.*/||')"
check "  ... wrote a run folder" [ -n "$RUN" ]
check "  ... run has summary.json" [ -f "$R/runs/$RUN/summary.json" ]
check "  ... last line says pushed" sh -c "grep -v '^[[:space:]]*\$' '$T/out' | tail -n 1 | grep -q '^pushed'"
check "  ... runs-mac has the run" remote_has "runs-mac:runs/$RUN/summary.json"
check "  ... runs-mac has the record log" sh -c "git -C '$T/remote.git' ls-tree -r --name-only runs-mac | grep -q 'record-plan.log'"
check "  ... model stopped afterwards" [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$LAB_PORT/health")" = 000 ]
lab record "$T/plan.json"; check "record again exits 0 (finished runs skipped)" [ "$(rc)" = 0 ]
lab push;                 check "push with nothing new says pushed" sh -c "grep -v '^[[:space:]]*\$' '$T/out' | tail -n 1 | grep -q '^pushed'"
lab status;               check "status shows the last run" out_has "Last run: $RUN"

echo "push fails cleanly without a remote"
git -C "$R" remote rename origin away
lab push;                 check "push prints NOT PUSHED" sh -c "grep -v '^[[:space:]]*\$' '$T/out' | tail -n 1 | grep -q '^NOT PUSHED: run ./lab push'"
git -C "$R" remote rename away origin

echo "push-media"
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -v error -f lavfi -i "sine=frequency=440:duration=2" -c:a aac "$T/voice.m4a"
  # constant 8 Mbit/s so the clip is about 3 MB; a 1 MB limit then forces the re-encode path
  ffmpeg -v error -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=3" -f lavfi -i "sine=duration=3" \
    -c:v libx264 -b:v 8M -minrate 8M -maxrate 8M -bufsize 2M -x264-params nal-hrd=cbr -c:a aac -shortest "$T/live.mov"
  (cd "$T" && LAB_MAX_FILE_BYTES=1000000 "$R/lab" push-media voice.m4a live.mov) > "$T/out" 2>&1; echo $? > "$T/rc"
  check "push-media exits 0" [ "$(rc)" = 0 ]
  check "  ... re-encoded the large file" out_has "Re-encoded to live.mp4"
  check "  ... media branch has both files" sh -c "remote() { git -C '$T/remote.git' \"\$@\"; }; remote cat-file -e media:media/voice.m4a && remote cat-file -e media:media/live.mp4"
  check "  ... media branch is an orphan" [ "$(git -C "$T/remote.git" rev-list --count media)" = 1 ]
else
  echo "  skip  push-media (no ffmpeg)"
fi

echo "update"
git clone -q "$T/remote.git" "$T/other" 2>/dev/null
git -C "$T/other" checkout -q main
mkdir -p "$T/other/runs/$RUN"
cp -R "$R/runs/$RUN/." "$T/other/runs/$RUN/"       # the lead ingests the Mac run into main
echo "new" > "$T/other/NEWFILE"
git -C "$T/other" add -A && git -C "$T/other" commit -q -m "ingest + change" && git -C "$T/other" push -q origin main
lab update;               check "update fast-forwards over identical untracked runs" [ "$(rc)" = 0 ]
check "  ... new file arrived" [ -f "$R/NEWFILE" ]
check "  ... run folder is now tracked" sh -c "git -C '$R' ls-files --error-unmatch 'runs/$RUN/summary.json' >/dev/null 2>&1"

echo "live"
lab live;                 check "live exits 0" [ "$(rc)" = 0 ]
check "  ... console answers /api/runs" [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$LAB_SERVE_PORT/api/runs")" = 200 ]
lab status;               check "status shows the console" out_has "Live console: http"
lab stop;                 check "stop exits 0" [ "$(rc)" = 0 ]
check "  ... model is down" [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$LAB_PORT/health")" = 000 ]

echo "rehearse"
lab doctor --rehearse;    check "doctor --rehearse exits 0" [ "$(rc)" = 0 ]
check "  ... writes doctor/*-rehearse.json" sh -c "ls '$R'/doctor/*-rehearse.json >/dev/null 2>&1"
check "  ... reports swap growth" out_has "swap growth with browser + recording"
lab stop

echo "verify (wrapper only; CI runs fortyshots verify on the real repository)"
(cd "$R" && LAB_VENV="$T/novenv" ./lab verify) > "$T/out" 2>&1; echo $? > "$T/rc"
check "verify without the package points to ./lab setup" out_has "Next: ./lab setup"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
