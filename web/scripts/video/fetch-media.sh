#!/usr/bin/env bash
# Copy the Mac recordings from the orphan branch `media` (written by `./lab push-media`) into media/.
#
#   web/scripts/video/fetch-media.sh            fetches origin/media and copies every file under media/
#
# `./lab push-media` re-encodes anything over 95 MB, so live.mov can arrive as live.mp4; assemble.sh looks for
# both. This only updates the remote-tracking ref origin/media and writes into media/ (gitignored).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
git fetch -q origin media || { echo "fetch-media: no branch 'media' on origin yet (run ./lab push-media on the Mac)" >&2; exit 1; }
mkdir -p media
n=0
while IFS= read -r path; do
  case "$path" in media/*) ;; *) continue ;; esac
  git show "origin/media:$path" >"media/${path#media/}"
  printf 'media/%s  (%s bytes)\n' "${path#media/}" "$(wc -c <"media/${path#media/}" | tr -d ' ')"
  n=$((n + 1))
done < <(git ls-tree -r --name-only origin/media)
[ "$n" -gt 0 ] || { echo "fetch-media: origin/media has no files under media/" >&2; exit 1; }
